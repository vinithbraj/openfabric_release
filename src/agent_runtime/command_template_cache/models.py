"""Typed models for learned shell command templates."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CommandTemplateStatus = Literal["success", "failure"]


class CommandTemplateLookupContext(BaseModel):
    """Request shape used to retrieve a reusable shell command template."""

    model_config = ConfigDict(extra="forbid")

    prompt: str
    step_description: str = ""
    mode: str = ""
    model_name: str = ""
    model_family: str = ""
    cwd: str = ""
    gateway_platform: str = ""
    task_type: str = ""
    tool_type: str = ""
    intent_type: str = ""
    tags: list[str] = Field(default_factory=list)
    lr_mode: str = "lr"
    limit: int = Field(default=3, ge=1)
    max_chars: int = Field(default=4000, ge=200)
    similarity_threshold: float = Field(default=0.82, ge=0.0, le=1.0)
    secondary_similarity_threshold: float = Field(default=0.15, ge=0.0, le=1.0)
    excluded_template_ids: list[str] = Field(default_factory=list)


class CommandTemplateEntry(BaseModel):
    """One persisted reusable shell command template."""

    model_config = ConfigDict(extra="forbid")

    template_id: str
    request_signature: str
    mode: str = ""
    model_name: str = ""
    model_family: str = ""
    cwd: str = ""
    gateway_platform: str = ""
    task_type: str = ""
    tool_type: str = ""
    intent_type: str = ""
    interaction_mode: str = ""
    tags: list[str] = Field(default_factory=list)
    lr_mode: str = "lr"
    payload_bindings: list[dict[str, Any]] = Field(default_factory=list)
    prompt_excerpt: str = ""
    step_excerpt: str = ""
    exact_step_key: str = ""
    exact_step_prompt_excerpt: str = ""
    command_template: str
    variables: list[dict[str, Any]] = Field(default_factory=list)
    direct_action: dict[str, Any] = Field(default_factory=dict)
    observed_command_hash: str = ""
    observed_command_excerpt: str = ""
    risk: str = ""
    effect_intent: str = ""
    effect_summary: str = ""
    requires_confirmation: bool = False
    approval_observed: bool = False
    status: CommandTemplateStatus = "success"
    failure_category: str = ""
    repair_notes: str = ""
    success_count: int = 0
    failure_count: int = 0
    use_count: int = 0
    created_at: str
    updated_at: str
    last_used_at: str = ""
    schema_version: int = 1


class CommandTemplateCandidate(BaseModel):
    """A command template entry plus retrieval score and match explanation."""

    model_config = ConfigDict(extra="forbid")

    entry: CommandTemplateEntry
    score: float = Field(ge=0.0, le=1.0)
    matched_tags: list[str] = Field(default_factory=list)
    reason: str = ""


class CommandTemplateWrite(BaseModel):
    """Payload written after a successful or failed learned-template use."""

    model_config = ConfigDict(extra="forbid")

    prompt: str
    step_description: str = ""
    mode: str = ""
    model_name: str = ""
    model_family: str = ""
    cwd: str = ""
    gateway_platform: str = ""
    task_type: str = ""
    tool_type: str = ""
    intent_type: str = ""
    interaction_mode: str = ""
    tags: list[str] = Field(default_factory=list)
    lr_mode: str = "lr"
    payload_bindings: list[dict[str, Any]] = Field(default_factory=list)
    exact_step_key: str = ""
    exact_step_prompt_excerpt: str = ""
    command_template: str
    variables: list[dict[str, Any]] = Field(default_factory=list)
    direct_action: dict[str, Any] = Field(default_factory=dict)
    observed_command: str = ""
    risk: str = ""
    effect_intent: str = ""
    effect_summary: str = ""
    requires_confirmation: bool = False
    approval_observed: bool = False
    status: CommandTemplateStatus = "success"
    failure_category: str = ""
    repair_notes: str = ""


class CommandTemplateStats(BaseModel):
    """Small API summary for the private command template cache."""

    model_config = ConfigDict(extra="forbid")

    total_entries: int = 0
    success_entries: int = 0
    failure_entries: int = 0
    total_uses: int = 0
    last_used_at: str = ""
    last_updated_at: str = ""
    last_cleared_at: str = ""
