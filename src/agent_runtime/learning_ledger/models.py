"""Typed models for the user-guided learning ledger."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


LearningOutcome = Literal[
    "success",
    "failure",
    "cancelled",
    "denied",
    "awaiting_confirmation",
    "awaiting_clarification",
    "failed_then_corrected",
    "user_corrected",
    "validation_failed",
    "gateway_failed",
    "no_learning_needed",
]
LearningLessonStatus = Literal["draft", "approved", "rejected", "retired"]
LearningLessonType = Literal[
    "command_correction",
    "negative_pattern",
    "validation_policy",
    "platform_guidance",
    "gateway_routing",
    "clarification_avoidance",
    "cache_boost",
    "performance_hint",
    "general",
]
LearningCacheType = Literal["plan", "computation", "command_template", "lr_t"]
LearningCacheStatus = Literal["neutral", "suspect", "strengthened", "quarantined", "deleted"]
CapabilityInsightType = Literal[
    "validator_failure",
    "corrected_action",
    "capability_gap",
    "user_feedback",
    "prompt_patch",
    "capability_patch",
]
CapabilityProposalTargetKind = Literal[
    "task_memory",
    "validation_policy",
    "prompt_patch",
    "capability_manifest_overlay",
    "executable_backend_patch",
]
CapabilityProposalStatus = Literal[
    "draft",
    "approved",
    "applied",
    "approved_pending_apply",
    "rejected",
    "retired",
]


class LearningRunWrite(BaseModel):
    """Payload used to persist one run's learning evidence."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    conversation_id: str = ""
    parent_request_id: str = ""
    agent_mode: str = ""
    prompt: str = ""
    prompt_signature: str = ""
    prompt_excerpt: str = ""
    model_name: str = ""
    model_family: str = ""
    gateway_id: str = ""
    gateway_nickname: str = ""
    gateway_node: str = ""
    gateway_platform: str = ""
    cwd: str = ""
    workspace_root: str = ""
    status: str = ""
    outcome: LearningOutcome = "no_learning_needed"
    duration_ms: float | None = None
    input_tokens_estimate: int = 0
    output_tokens_estimate: int = 0
    total_tokens_estimate: int = 0
    llm_call_count: int = 0
    confirmation_required: bool = False
    clarification_required: bool = False
    auto_approved: bool = False
    memory_ids: list[str] = Field(default_factory=list)
    cache_ids: list[str] = Field(default_factory=list)
    error: str = ""
    final_response_preview: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class LearningRun(BaseModel):
    """One durable run record in the learning ledger."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    conversation_id: str = ""
    parent_request_id: str = ""
    agent_mode: str = ""
    prompt_signature: str = ""
    prompt_excerpt: str = ""
    model_name: str = ""
    model_family: str = ""
    gateway_id: str = ""
    gateway_nickname: str = ""
    gateway_node: str = ""
    gateway_platform: str = ""
    cwd: str = ""
    workspace_root: str = ""
    status: str = ""
    outcome: LearningOutcome = "no_learning_needed"
    duration_ms: float | None = None
    input_tokens_estimate: int = 0
    output_tokens_estimate: int = 0
    total_tokens_estimate: int = 0
    llm_call_count: int = 0
    confirmation_required: bool = False
    clarification_required: bool = False
    auto_approved: bool = False
    memory_ids: list[str] = Field(default_factory=list)
    cache_ids: list[str] = Field(default_factory=list)
    error: str = ""
    final_response_preview: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class LearningActionWrite(BaseModel):
    """Payload for one action outcome attached to a run."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    action_id: str = ""
    task_id: str = ""
    kind: str = ""
    title: str = ""
    status: str = ""
    cwd: str = ""
    command_hash: str = ""
    exit_code: int | None = None
    error_type: str = ""
    error_preview: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class LearningActionOutcome(LearningActionWrite):
    """One persisted action outcome."""

    id: int = 0
    created_at: str


class LearningCacheEventWrite(BaseModel):
    """Payload for cache usage or cache outcome evidence."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    cache_type: LearningCacheType
    cache_id: str
    event: str = ""
    status: LearningCacheStatus = "neutral"
    outcome: str = ""
    score: float | None = None
    reason: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class LearningCacheEvent(LearningCacheEventWrite):
    """One persisted cache event."""

    id: int = 0
    created_at: str
    updated_at: str


class LearningLessonWrite(BaseModel):
    """Payload used to create or update one proposed lesson."""

    model_config = ConfigDict(extra="forbid")

    lesson_type: LearningLessonType = "general"
    title: str
    instruction: str
    summary: str = ""
    scope: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source_request_id: str = ""
    tags: list[str] = Field(default_factory=list)
    rationale: str = ""
    dedupe_key: str = ""


class LearningLesson(BaseModel):
    """One user-guided lesson lifecycle record."""

    model_config = ConfigDict(extra="forbid")

    lesson_id: str
    status: LearningLessonStatus = "draft"
    lesson_type: LearningLessonType = "general"
    title: str
    instruction: str
    summary: str = ""
    scope: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.5
    source_request_id: str = ""
    mirrored_memory_id: str = ""
    auto_approved: bool = False
    tags: list[str] = Field(default_factory=list)
    rationale: str = ""
    rejection_reason: str = ""
    dedupe_key: str = ""
    created_at: str
    updated_at: str


class CapabilityInsightWrite(BaseModel):
    """Normalized learning evidence used to draft capability evolution proposals."""

    model_config = ConfigDict(extra="forbid")

    insight_type: CapabilityInsightType
    source_request_id: str = ""
    source_stage: str = ""
    source_event_type: str = ""
    source_error_type: str = ""
    target_kind: CapabilityProposalTargetKind | None = None
    target_id: str = ""
    prompt_key: str = ""
    capability_id: str = ""
    model_name: str = ""
    model_family: str = ""
    gateway_id: str = ""
    cwd: str = ""
    summary: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    dedupe_key: str = ""


class CapabilityInsight(CapabilityInsightWrite):
    """One persisted capability evolution evidence record."""

    insight_id: str
    created_at: str
    updated_at: str


class CapabilityProposalWrite(BaseModel):
    """Payload used to create one capability evolution proposal."""

    model_config = ConfigDict(extra="forbid")

    target_kind: CapabilityProposalTargetKind
    title: str
    summary: str = ""
    rationale: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source: Literal["deterministic", "llm", "user"] = "deterministic"
    source_request_id: str = ""
    source_insight_id: str = ""
    target_id: str = ""
    draft: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    safety_decision: dict[str, Any] = Field(default_factory=dict)
    dedupe_key: str = ""


class CapabilityProposal(CapabilityProposalWrite):
    """One persisted capability evolution proposal and application state."""

    proposal_id: str
    status: CapabilityProposalStatus = "draft"
    auto_approved: bool = False
    applied_ref: str = ""
    apply_error: str = ""
    created_at: str
    updated_at: str


class LearningSummary(BaseModel):
    """Small dashboard summary for the learning ledger."""

    model_config = ConfigDict(extra="forbid")

    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    draft_lessons: int = 0
    approved_lessons: int = 0
    rejected_lessons: int = 0
    retired_lessons: int = 0
    auto_approved_lessons: int = 0
    draft_proposals: int = 0
    approved_proposals: int = 0
    applied_proposals: int = 0
    rejected_proposals: int = 0
    retired_proposals: int = 0
    auto_approved_proposals: int = 0
    suspect_cache_entries: int = 0
    strengthened_cache_entries: int = 0
    last_run_at: str = ""
    last_lesson_at: str = ""
    last_proposal_at: str = ""
