"""Typed models for the private operator plan cache."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


PlanCacheStatus = Literal["success", "failure"]


class PlanCacheLookupContext(BaseModel):
    """Request shape used to find reusable operator plan structure."""

    model_config = ConfigDict(extra="forbid")

    prompt: str
    mode: str = ""
    model_name: str = ""
    model_family: str = ""
    cwd: str = ""
    task_type: str = ""
    tool_type: str = ""
    intent_type: str = ""
    tags: list[str] = Field(default_factory=list)
    limit: int = Field(default=3, ge=1)
    max_chars: int = Field(default=4000, ge=200)
    similarity_threshold: float = Field(default=0.86, ge=0.0, le=1.0)
    excluded_cache_ids: list[str] = Field(default_factory=list)


class PlanCacheEntry(BaseModel):
    """One persisted reusable operator plan cache entry."""

    model_config = ConfigDict(extra="forbid")

    cache_id: str
    request_signature: str
    mode: str = ""
    model_name: str = ""
    model_family: str = ""
    cwd: str = ""
    task_type: str = ""
    tool_type: str = ""
    intent_type: str = ""
    tags: list[str] = Field(default_factory=list)
    prompt_excerpt: str = ""
    self_brief: dict[str, Any] = Field(default_factory=dict)
    plan: dict[str, Any] = Field(default_factory=dict)
    plan_fingerprint: str = ""
    action_summaries: list[dict[str, Any]] = Field(default_factory=list)
    risk_summary: str = ""
    records_summary: list[dict[str, Any]] = Field(default_factory=list)
    final_response_preview: str = ""
    exact_step_key: str = ""
    exact_step_prompt_excerpt: str = ""
    intent_snapshot: dict[str, Any] = Field(default_factory=dict)
    intent_signature: str = ""
    direct_plan: dict[str, Any] = Field(default_factory=dict)
    status: PlanCacheStatus = "success"
    failure_category: str = ""
    repair_notes: str = ""
    success_count: int = 0
    failure_count: int = 0
    use_count: int = 0
    created_at: str
    updated_at: str
    last_used_at: str = ""
    schema_version: int = 1


class PlanCacheCandidate(BaseModel):
    """A cache entry plus retrieval score and match explanation."""

    model_config = ConfigDict(extra="forbid")

    entry: PlanCacheEntry
    score: float = Field(ge=0.0, le=1.0)
    matched_tags: list[str] = Field(default_factory=list)
    reason: str = ""


class PlanCacheWrite(BaseModel):
    """Payload written after a request completes or fails."""

    model_config = ConfigDict(extra="forbid")

    prompt: str
    mode: str = ""
    model_name: str = ""
    model_family: str = ""
    cwd: str = ""
    task_type: str = ""
    tool_type: str = ""
    intent_type: str = ""
    tags: list[str] = Field(default_factory=list)
    self_brief: dict[str, Any] = Field(default_factory=dict)
    plan: dict[str, Any] = Field(default_factory=dict)
    records: list[dict[str, Any]] = Field(default_factory=list)
    final_response: str = ""
    exact_step_key: str = ""
    exact_step_prompt_excerpt: str = ""
    intent_snapshot: dict[str, Any] = Field(default_factory=dict)
    intent_signature: str = ""
    direct_plan: dict[str, Any] = Field(default_factory=dict)
    status: PlanCacheStatus = "success"
    failure_category: str = ""
    repair_notes: str = ""


class PlanCacheStats(BaseModel):
    """Small API summary for the private operator cache."""

    model_config = ConfigDict(extra="forbid")

    total_entries: int = 0
    success_entries: int = 0
    failure_entries: int = 0
    total_uses: int = 0
    last_used_at: str = ""
    last_updated_at: str = ""
    last_cleared_at: str = ""
