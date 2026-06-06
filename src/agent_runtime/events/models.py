"""Typed scheduled-event contracts used by the Agent UI."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


EventStatus = Literal["active", "paused", "cancelled", "completed", "deleted"]
EventRunStatus = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "awaiting_confirmation",
    "awaiting_clarification",
    "cancelled",
    "skipped",
]
EventScheduleType = Literal["interval", "once"]
EventKind = Literal["scheduled", "todo"]
EventActionType = Literal["agent_prompt", "notification"]
EventNotifyOn = Literal[
    "completed",
    "failed",
    "cancelled",
    "skipped",
    "awaiting_confirmation",
    "awaiting_clarification",
]
NotificationLevel = Literal["info", "success", "warning", "error"]
NotificationStatus = Literal["unread", "read", "dismissed"]


DEFAULT_EVENT_NOTIFY_ON: list[EventNotifyOn] = [
    "failed",
    "cancelled",
    "skipped",
    "awaiting_confirmation",
    "awaiting_clarification",
]


class AgentEventDraft(BaseModel):
    """One editable event proposal extracted from user text."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=160)
    prompt: str = Field(default="", max_length=24000)
    schedule_type: EventScheduleType = "interval"
    event_kind: EventKind = "scheduled"
    interval_seconds: int = Field(default=3600, ge=1)
    timezone: str = Field(default="UTC", max_length=80)
    next_run_at: str = Field(default="", max_length=80)
    schedule_summary: str = Field(default="", max_length=240)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_details: list[str] = Field(default_factory=list)
    auto_approve_confirmations: bool = True
    context: dict[str, Any] = Field(default_factory=dict)
    action_type: EventActionType = "agent_prompt"
    notification_message: str = Field(default="", max_length=24000)
    notify_on: list[EventNotifyOn] = Field(default_factory=lambda: list(DEFAULT_EVENT_NOTIFY_ON))

    @model_validator(mode="after")
    def _normalize_notification_message(self) -> "AgentEventDraft":
        if self.action_type == "notification" and not self.notification_message.strip():
            self.notification_message = self.prompt
        return self


class AgentEventDraftRequest(BaseModel):
    """Prompt and request context used to draft scheduled events."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=24000)
    context: dict[str, Any] = Field(default_factory=dict)
    agent_mode: Literal["standard", "llm_operator"] = "standard"
    llm_model: str | None = Field(default=None, max_length=240)


class AgentEventDraftResponse(BaseModel):
    """LLM/fallback response for schedule extraction."""

    model_config = ConfigDict(extra="forbid")

    is_schedule_request: bool = False
    drafts: list[AgentEventDraft] = Field(default_factory=list)
    missing_details: list[str] = Field(default_factory=list)
    rationale: str = Field(default="", max_length=1000)


class AgentEventCreate(BaseModel):
    """Create a persistent scheduled event."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=160)
    prompt: str = Field(min_length=1, max_length=24000)
    status: EventStatus = "active"
    schedule_type: EventScheduleType = "interval"
    event_kind: EventKind = "scheduled"
    interval_seconds: int = Field(default=3600, ge=1)
    timezone: str = Field(default="UTC", max_length=80)
    next_run_at: str = Field(default="", max_length=80)
    context: dict[str, Any] = Field(default_factory=dict)
    auto_approve_confirmations: bool = True
    action_type: EventActionType = "agent_prompt"
    notification_message: str = Field(default="", max_length=24000)
    notify_on: list[EventNotifyOn] = Field(default_factory=lambda: list(DEFAULT_EVENT_NOTIFY_ON))

    @model_validator(mode="after")
    def _normalize_status(self) -> "AgentEventCreate":
        if self.status == "deleted":
            self.status = "cancelled"
        if self.action_type == "notification" and not self.notification_message.strip():
            self.notification_message = self.prompt
        return self


class AgentEventUpdate(BaseModel):
    """Patch a persistent scheduled event."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=160)
    prompt: str | None = Field(default=None, min_length=1, max_length=24000)
    status: EventStatus | None = None
    schedule_type: EventScheduleType | None = None
    event_kind: EventKind | None = None
    interval_seconds: int | None = Field(default=None, ge=1)
    timezone: str | None = Field(default=None, max_length=80)
    next_run_at: str | None = Field(default=None, max_length=80)
    context: dict[str, Any] | None = None
    auto_approve_confirmations: bool | None = None
    action_type: EventActionType | None = None
    notification_message: str | None = Field(default=None, max_length=24000)
    notify_on: list[EventNotifyOn] | None = None


class AgentEventRecord(BaseModel):
    """One stored scheduled event."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    title: str
    prompt: str
    status: EventStatus = "active"
    schedule_type: EventScheduleType = "interval"
    event_kind: EventKind = "scheduled"
    interval_seconds: int = Field(default=3600, ge=1)
    timezone: str = "UTC"
    next_run_at: str = ""
    last_run_at: str = ""
    created_at: str = ""
    updated_at: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    auto_approve_confirmations: bool = True
    action_type: EventActionType = "agent_prompt"
    notification_message: str = ""
    notify_on: list[EventNotifyOn] = Field(default_factory=lambda: list(DEFAULT_EVENT_NOTIFY_ON))


class AgentEventRunRecord(BaseModel):
    """One stored scheduled-event run attempt."""

    model_config = ConfigDict(extra="forbid")

    event_run_id: str
    event_id: str
    task_id: str = ""
    request_id: str = ""
    parent_request_id: str = ""
    scheduled_for: str = ""
    started_at: str = ""
    completed_at: str = ""
    status: EventRunStatus = "running"
    final_response_preview: str = ""
    error_preview: str = ""
    trace_url: str = ""
    created_at: str = ""
    updated_at: str = ""


class AgentNotificationCreate(BaseModel):
    """Create one persisted in-app notification."""

    model_config = ConfigDict(extra="forbid")

    level: NotificationLevel = "info"
    title: str = Field(default="", max_length=160)
    message: str = Field(min_length=1, max_length=24000)
    source_type: str = Field(default="system", max_length=80)
    source_id: str = Field(default="", max_length=160)
    event_id: str = Field(default="", max_length=160)
    event_run_id: str = Field(default="", max_length=160)
    request_id: str = Field(default="", max_length=160)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentNotificationUpdate(BaseModel):
    """Patch one persisted in-app notification."""

    model_config = ConfigDict(extra="forbid")

    status: NotificationStatus | None = None


class AgentNotificationRecord(BaseModel):
    """One persisted in-app notification."""

    model_config = ConfigDict(extra="forbid")

    notification_id: str
    level: NotificationLevel = "info"
    title: str = ""
    message: str = ""
    source_type: str = "system"
    source_id: str = ""
    event_id: str = ""
    event_run_id: str = ""
    request_id: str = ""
    status: NotificationStatus = "unread"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
