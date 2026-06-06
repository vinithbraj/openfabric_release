"""Typed models for the local Agent Parameter Store."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ParameterOperation = Literal["create", "update", "delete", "get"]


class AgentParameterRecord(BaseModel):
    """One durable key/value parameter entry."""

    model_config = ConfigDict(extra="forbid")

    key: str
    normalized_key: str
    value_json: dict[str, Any] = Field(default_factory=dict)
    context_json: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    sensitive: bool = True
    created_at: str
    updated_at: str
    last_used_at: str = ""
    use_count: int = 0


class AgentParameterCreate(BaseModel):
    """Payload for creating a parameter entry."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=160)
    value_json: dict[str, Any] = Field(default_factory=dict)
    context_json: dict[str, Any] = Field(default_factory=dict)
    description: str = Field(default="", max_length=2000)
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    sensitive: bool = True

    @field_validator("value_json")
    @classmethod
    def value_must_be_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict) or not value:
            raise ValueError("value_json must be a non-empty JSON object")
        return value

    @field_validator("context_json")
    @classmethod
    def context_must_be_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("context_json must be a JSON object")
        return value


class AgentParameterUpdate(BaseModel):
    """Partial update payload for one parameter entry."""

    model_config = ConfigDict(extra="forbid")

    key: str | None = Field(default=None, min_length=1, max_length=160)
    value_json: dict[str, Any] | None = None
    context_json: dict[str, Any] | None = None
    description: str | None = Field(default=None, max_length=2000)
    aliases: list[str] | None = None
    tags: list[str] | None = None
    sensitive: bool | None = None

    @field_validator("value_json")
    @classmethod
    def value_update_must_be_object(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and (not isinstance(value, dict) or not value):
            raise ValueError("value_json must be a non-empty JSON object")
        return value

    @field_validator("context_json")
    @classmethod
    def context_update_must_be_object(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and not isinstance(value, dict):
            raise ValueError("context_json must be a JSON object")
        return value


class AgentParameterAuditEvent(BaseModel):
    """One immutable parameter audit event."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    key: str = ""
    normalized_key: str = ""
    event_type: str
    actor: str = "user"
    timestamp: str
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentParameterSummary(BaseModel):
    """LLM/UI-safe masked parameter summary."""

    model_config = ConfigDict(extra="forbid")

    key: str
    normalized_key: str
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    sensitive: bool = True
    value_schema: dict[str, Any] = Field(default_factory=dict)
    masked_value_json: dict[str, Any] = Field(default_factory=dict)
    context_json: dict[str, Any] = Field(default_factory=dict)
    context_schema: dict[str, Any] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    last_used_at: str = ""
    use_count: int = 0


class AgentParameterDraftRequest(BaseModel):
    """Request to draft one parameter store operation from a prompt."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=24000)
    context: dict[str, Any] = Field(default_factory=dict)
    agent_mode: Literal["standard", "llm_operator", "advisory"] = "llm_operator"
    llm_model: str | None = None


class AgentParameterDraft(BaseModel):
    """LLM-authored editable parameter draft."""

    model_config = ConfigDict(extra="forbid")

    operation: ParameterOperation = "create"
    key: str = ""
    value: dict[str, Any] = Field(default_factory=dict)
    context_json: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    sensitive: bool = True
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_details: list[str] = Field(default_factory=list)
    rationale: str = ""


class AgentParameterDraftResponse(BaseModel):
    """Response envelope for parameter extraction."""

    model_config = ConfigDict(extra="forbid")

    is_parameter_request: bool = False
    drafts: list[AgentParameterDraft] = Field(default_factory=list)
    missing_details: list[str] = Field(default_factory=list)
    rationale: str = ""


class AgentParameterMatch(BaseModel):
    """One retrieved parameter and scoring diagnostics."""

    model_config = ConfigDict(extra="forbid")

    record: AgentParameterRecord
    summary: AgentParameterSummary
    score: int = 0
    exact: bool = False
    match_reasons: list[str] = Field(default_factory=list)
