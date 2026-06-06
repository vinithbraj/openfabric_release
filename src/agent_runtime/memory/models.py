"""Typed models for persistent agent memory."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


MemoryStatus = Literal["active", "proposed", "retired"]
MemoryScope = Literal["exact_model", "model_family", "global"]
MemoryKind = Literal["task_memory", "preference_memory", "validation_policy"]
MemoryProvenance = Literal["manual", "feedback", "optimizer", "learning_ledger"]
MemoryProposalType = Literal["create", "update", "retire", "restore"]
MemoryProposalStatus = Literal["proposed", "applied", "rejected"]
MemoryDirectiveStrength = Literal["advisory", "must_consider", "required_unless_conflict"]
MemoryDirectiveTarget = Literal[
    "direct_answer",
    "clarification",
    "operator_plan",
    "plan_review",
    "final_answer",
    "cache",
    "repair",
    "code_generation",
    "evidence_review",
    "answer_judge",
]
MemoryRunFeedbackOutcome = Literal[
    "right_decision",
    "partially_right",
    "wrong_decision",
    "unclear",
]


class MemoryEntry(BaseModel):
    """One durable instruction or lesson available to the agent."""

    model_config = ConfigDict(extra="forbid")

    memory_id: str
    instruction: str
    summary: str = ""
    status: MemoryStatus = "active"
    memory_kind: MemoryKind = "task_memory"
    scope: MemoryScope = "global"
    model_name: str = ""
    model_family: str = ""
    task_type: str = ""
    tool_type: str = ""
    intent_type: str = ""
    validator_error_type: str = ""
    safe_examples: list[str] = Field(default_factory=list)
    blocked_examples: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    provenance: MemoryProvenance = "manual"
    request_id: str = ""
    rationale: str = ""
    created_at: str
    updated_at: str
    use_count: int = 0
    last_used_at: str = ""


class MemoryEntryCreate(BaseModel):
    """Payload for creating one memory entry."""

    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(min_length=1)
    summary: str = ""
    status: MemoryStatus = "active"
    memory_kind: MemoryKind = "task_memory"
    scope: MemoryScope = "global"
    model_name: str = ""
    model_family: str = ""
    task_type: str = ""
    tool_type: str = ""
    intent_type: str = ""
    validator_error_type: str = ""
    safe_examples: list[str] = Field(default_factory=list)
    blocked_examples: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    provenance: MemoryProvenance = "manual"
    request_id: str = ""
    rationale: str = ""


class MemoryEntryUpdate(BaseModel):
    """Partial update payload for one memory entry."""

    model_config = ConfigDict(extra="forbid")

    instruction: str | None = None
    summary: str | None = None
    memory_kind: MemoryKind | None = None
    scope: MemoryScope | None = None
    model_name: str | None = None
    model_family: str | None = None
    task_type: str | None = None
    tool_type: str | None = None
    intent_type: str | None = None
    validator_error_type: str | None = None
    safe_examples: list[str] | None = None
    blocked_examples: list[str] | None = None
    tags: list[str] | None = None
    rationale: str | None = None


class MemoryAuditEvent(BaseModel):
    """One immutable audit record for a memory entry or proposal."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    memory_id: str = ""
    proposal_id: str = ""
    event_type: str
    actor: str = "user"
    timestamp: str
    payload: dict[str, Any] = Field(default_factory=dict)


class MemoryProposal(BaseModel):
    """A pending LLM-authored or optimizer-authored memory change."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str
    proposal_type: MemoryProposalType = "create"
    status: MemoryProposalStatus = "proposed"
    memory_id: str = ""
    draft: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    provenance: MemoryProvenance = "feedback"
    created_at: str
    updated_at: str


class MemoryRetrievalContext(BaseModel):
    """Bounded request context used to retrieve relevant memory."""

    model_config = ConfigDict(extra="forbid")

    prompt: str
    memory_kind: MemoryKind | None = None
    model_name: str = ""
    model_family: str = ""
    task_type: str = ""
    tool_type: str = ""
    intent_type: str = ""
    validator_error_type: str = ""
    tags: list[str] = Field(default_factory=list)
    limit: int = Field(default=8, ge=1)
    max_chars: int = Field(default=3000, ge=200)


class MemoryRetrievalMatch(BaseModel):
    """One retrieved memory plus explainable scoring details."""

    model_config = ConfigDict(extra="forbid")

    entry: MemoryEntry
    score: int = 0
    scope_score: int = 0
    relevance_score: int = 0
    match_reasons: list[str] = Field(default_factory=list)
    structured_matches: list[str] = Field(default_factory=list)
    tag_overlap: list[str] = Field(default_factory=list)
    text_overlap: list[str] = Field(default_factory=list)
    text_overlap_count: int = 0


class MemoryDirective(BaseModel):
    """Runtime-only actionable memory instruction for prompts and reviews."""

    model_config = ConfigDict(extra="forbid")

    memory_id: str
    instruction: str
    memory_kind: MemoryKind = "task_memory"
    applies_to: list[MemoryDirectiveTarget] = Field(default_factory=list)
    strength: MemoryDirectiveStrength = "advisory"
    summary: str = ""
    safe_examples: list[str] = Field(default_factory=list)
    blocked_examples: list[str] = Field(default_factory=list)
    match_reasons: list[str] = Field(default_factory=list)
    score: int = 0


class MemoryRunFeedback(BaseModel):
    """Typed post-run feedback context supplied by the UI."""

    model_config = ConfigDict(extra="forbid")

    feedback_kind: Literal["post_run"] = "post_run"
    outcome: MemoryRunFeedbackOutcome = "unclear"
    request_id: str = ""
    run_status: str = ""
    prompt: str = ""
    final_response: str = ""
    error: str = ""
    model_name: str = ""
    applied_memory_ids: list[str] = Field(default_factory=list)
    applied_memory_count: int = Field(default=0, ge=0)
    applied_memory_use_count: int = Field(default=0, ge=0)


class MemoryFeedbackRequest(BaseModel):
    """User feedback used to propose a memory draft."""

    model_config = ConfigDict(extra="forbid")

    feedback: str = Field(min_length=1)
    feedback_target: Literal["memory", "validation_policy"] = "memory"
    prompt: str = ""
    request_id: str = ""
    model_name: str = ""
    llm_model: str = ""
    llm_base_url: str = ""
    task_type: str = ""
    tool_type: str = ""
    intent_type: str = ""
    validator_error_type: str = ""
    run_feedback: MemoryRunFeedback | None = None


class MemoryFeedbackDraftRequest(BaseModel):
    """Request to draft editable post-run feedback text."""

    model_config = ConfigDict(extra="forbid")

    outcome: MemoryRunFeedbackOutcome = "unclear"
    prompt: str = ""
    request_id: str = ""
    model_name: str = ""
    llm_model: str = ""
    llm_base_url: str = ""
    run_feedback: MemoryRunFeedback | None = None


class MemoryFeedbackDraftResponse(BaseModel):
    """LLM-authored editable feedback suggestion."""

    model_config = ConfigDict(extra="forbid")

    feedback: str = ""
    rationale: str = ""


class MemoryOptimizationRequest(BaseModel):
    """Request to review and propose memory cleanup."""

    model_config = ConfigDict(extra="forbid")

    memory_ids: list[str] = Field(default_factory=list)
    query: str = ""


class MemoryContextDraftRequest(BaseModel):
    """Request/response text used to prefill memory association fields."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = ""
    final_response: str = ""
    request_id: str = ""
    model_name: str = ""


class MemoryContextDraftResponse(BaseModel):
    """LLM-authored metadata for a memory draft, not an applied memory."""

    model_config = ConfigDict(extra="forbid")

    instruction: str = ""
    summary: str = ""
    scope: MemoryScope = "global"
    model_name: str = ""
    model_family: str = ""
    task_type: str = ""
    tool_type: str = ""
    intent_type: str = ""
    tags: list[str] = Field(default_factory=list)
    rationale: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class MemoryDraftProposal(BaseModel):
    """LLM-authored proposal payload for one new or revised memory."""

    model_config = ConfigDict(extra="forbid")

    proposal_type: MemoryProposalType = "create"
    memory_id: str = ""
    instruction: str = ""
    summary: str = ""
    memory_kind: MemoryKind = "task_memory"
    scope: MemoryScope = "global"
    model_name: str = ""
    model_family: str = ""
    task_type: str = ""
    tool_type: str = ""
    intent_type: str = ""
    validator_error_type: str = ""
    safe_examples: list[str] = Field(default_factory=list)
    blocked_examples: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    rationale: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class MemoryDraftResponse(BaseModel):
    """Structured LLM response for feedback-to-memory."""

    model_config = ConfigDict(extra="forbid")

    drafts: list[MemoryDraftProposal] = Field(default_factory=list)
    rationale: str = ""
