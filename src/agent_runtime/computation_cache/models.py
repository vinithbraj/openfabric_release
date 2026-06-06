"""Typed models for reusable deferred-computation templates."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ComputationCacheStatus = Literal["success", "failure"]


class ComputationCacheLookupContext(BaseModel):
    """Request and runtime-input shape used to find computation templates."""

    model_config = ConfigDict(extra="forbid")

    prompt: str
    mode: str = ""
    model_name: str = ""
    model_family: str = ""
    task_type: str = ""
    tool_type: str = ""
    intent_type: str = ""
    tags: list[str] = Field(default_factory=list)
    action_kind: str = ""
    action_reason: str = ""
    input_profile: dict[str, Any] = Field(default_factory=dict)
    input_signature: str = ""
    limit: int = Field(default=3, ge=1)
    max_chars: int = Field(default=4000, ge=200)
    similarity_threshold: float = Field(default=0.84, ge=0.0, le=1.0)
    excluded_cache_ids: list[str] = Field(default_factory=list)


class ComputationCacheEntry(BaseModel):
    """One durable reusable computation-code template."""

    model_config = ConfigDict(extra="forbid")

    cache_id: str
    request_signature: str
    mode: str = ""
    model_name: str = ""
    model_family: str = ""
    task_type: str = ""
    tool_type: str = ""
    intent_type: str = ""
    tags: list[str] = Field(default_factory=list)
    action_kind: str = ""
    action_reason: str = ""
    input_profile: dict[str, Any] = Field(default_factory=dict)
    input_signature: str = ""
    exact_step_key: str = ""
    exact_step_prompt_excerpt: str = ""
    prompt_excerpt: str = ""
    code_template: str = ""
    direct_action: dict[str, Any] = Field(default_factory=dict)
    declared_output_shape: str = "text"
    allow_zero_result: bool = False
    template_reason: str = ""
    output_preview: str = ""
    status: ComputationCacheStatus = "success"
    failure_category: str = ""
    repair_notes: str = ""
    success_count: int = 0
    failure_count: int = 0
    use_count: int = 0
    created_at: str
    updated_at: str
    last_used_at: str = ""
    schema_version: int = 1


class ComputationCacheCandidate(BaseModel):
    """A computation cache entry plus score and explanation."""

    model_config = ConfigDict(extra="forbid")

    entry: ComputationCacheEntry
    score: float = Field(ge=0.0, le=1.0)
    matched_tags: list[str] = Field(default_factory=list)
    reason: str = ""


class ComputationCacheWrite(BaseModel):
    """Payload written after generated computation code succeeds or fails."""

    model_config = ConfigDict(extra="forbid")

    prompt: str
    mode: str = ""
    model_name: str = ""
    model_family: str = ""
    task_type: str = ""
    tool_type: str = ""
    intent_type: str = ""
    tags: list[str] = Field(default_factory=list)
    action_kind: str = ""
    action_reason: str = ""
    input_profile: dict[str, Any] = Field(default_factory=dict)
    input_signature: str = ""
    exact_step_key: str = ""
    exact_step_prompt_excerpt: str = ""
    code_template: str = ""
    direct_action: dict[str, Any] = Field(default_factory=dict)
    declared_output_shape: str = "text"
    allow_zero_result: bool = False
    template_reason: str = ""
    output: Any = None
    status: ComputationCacheStatus = "success"
    failure_category: str = ""
    repair_notes: str = ""


class ComputationCacheStats(BaseModel):
    """Small API summary for the private computation template cache."""

    model_config = ConfigDict(extra="forbid")

    total_entries: int = 0
    success_entries: int = 0
    failure_entries: int = 0
    total_uses: int = 0
    last_used_at: str = ""
    last_updated_at: str = ""
    last_cleared_at: str = ""
