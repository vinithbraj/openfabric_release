"""Typed contracts for runtime reliability, recovery, and verification."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


FailureKind = Literal[
    "schema_failure",
    "json_failure",
    "validation_failure",
    "placeholder_command",
    "cwd_not_found",
    "dependency_failure",
    "command_failed",
    "command_not_found",
    "python_failed",
    "deferred_python_failed",
    "verification_failed",
    "completion_failed",
    "formatting_failed",
    "authoritative_value_dropped",
    "model_looping_or_low_confidence",
    "budget_exhausted",
    "approval_envelope_violation",
    "unknown",
]

ReliabilityEventKind = Literal[
    "failure_detected",
    "plan_compiled",
    "recovery_decided",
    "recovery_accepted",
    "recovery_rejected",
    "probe_run",
    "evidence_audited",
    "coverage_reviewed",
    "formatter_retried",
    "verification_completed",
    "approval_envelope_created",
    "approval_envelope_violation",
    "outcome_recorded",
]

RecoveryAction = Literal[
    "retry_same",
    "repair_plan",
    "split_plan",
    "run_probe",
    "switch_operator_shape",
    "continue_with_seed_records",
    "ask_user",
    "block",
    "finalize",
]

VerificationStatus = Literal["satisfied", "partially_satisfied", "unsatisfied", "unknown"]
ReliabilityMode = Literal["off", "standard", "aggressive"]
EvidenceSensitivity = Literal["public", "sensitive", "redacted"]
CoverageVerdict = Literal["accept", "repair_required", "fail"]
ObligationStatus = Literal["not_applicable", "covered", "missing", "unknown"]


class RecoveryBudget(BaseModel):
    """Request-local autonomous recovery budget."""

    model_config = ConfigDict(extra="forbid")

    validation_repairs: int = 0
    schema_repairs: int = 0
    execution_repairs: int = 0
    probes: int = 0
    completion_continuations: int = 0
    rephrases: int = 0
    mutation_envelope_uses: int = 0
    max_validation_repairs: int = 1
    max_schema_repairs: int = 1
    max_execution_repairs: int = 1
    max_probes: int = 3
    max_completion_continuations: int = 1
    max_rephrases: int = 1
    max_mutation_envelope_uses: int = 2

    def exhausted(self, field: str) -> bool:
        """Return whether one budget counter has reached its limit."""

        return int(getattr(self, field, 0)) >= int(getattr(self, f"max_{field}", 0))


class RecoveryDecision(BaseModel):
    """Deterministic recovery decision for one normalized failure."""

    model_config = ConfigDict(extra="forbid")

    action: RecoveryAction
    failure_kind: FailureKind = "unknown"
    reason: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    budget_field: str = ""
    requires_confirmation: bool = False
    evidence_refs: list[str] = Field(default_factory=list)


class OutcomeVerification(BaseModel):
    """Final answer and execution-evidence satisfaction result."""

    model_config = ConfigDict(extra="forbid")

    status: VerificationStatus
    reason: str
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    obligations: list["EvidenceObligation"] = Field(default_factory=list)
    missing_obligations: list[str] = Field(default_factory=list)
    coverage_review: "AnswerCoverageReview | None" = None
    obligation_status: ObligationStatus = "not_applicable"


class EvidenceObligation(BaseModel):
    """One semantic fact the final answer may need to preserve."""

    model_config = ConfigDict(extra="forbid")

    obligation_id: str
    source_action_id: str = ""
    label: str
    value_summary: str = ""
    raw_evidence_ref: str = ""
    must_report: bool = True
    sensitivity: EvidenceSensitivity = "public"
    coverage_hint: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class AnswerObligationSet(BaseModel):
    """LLM-audited answer obligations derived from execution evidence."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = ""
    facts: list[EvidenceObligation] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)
    source_record_ids: list[str] = Field(default_factory=list)
    audit_status: Literal["complete", "unknown", "failed"] = "complete"
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class AnswerCoverageReview(BaseModel):
    """LLM-authored review of whether a final answer covers obligations."""

    model_config = ConfigDict(extra="forbid")

    verdict: CoverageVerdict
    covered_obligations: list[str] = Field(default_factory=list)
    missing_obligations: list[str] = Field(default_factory=list)
    contradicted_obligations: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    repair_instruction: str = ""
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ReliabilityEvent(BaseModel):
    """Persisted request-timeline event for reliability inspection."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    request_id: str
    event_kind: ReliabilityEventKind
    failure_kind: FailureKind | None = None
    recovery_action: RecoveryAction | None = None
    stage: str = ""
    title: str
    summary: str = ""
    model_id: str = "unknown"
    status: str = "info"
    evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


class ModelCapabilityProfile(BaseModel):
    """Aggregated model reliability counters used to adapt runtime behavior."""

    model_config = ConfigDict(extra="forbid")

    model_id: str
    total_events: int = 0
    total_failures: int = 0
    total_recoveries: int = 0
    recovered_successes: int = 0
    schema_failures: int = 0
    json_failures: int = 0
    placeholder_failures: int = 0
    command_failures: int = 0
    python_failures: int = 0
    verification_failures: int = 0
    formatting_failures: int = 0
    low_confidence_failures: int = 0
    json_validity_rate: float = 1.0
    schema_repair_rate: float = 0.0
    placeholder_rate: float = 0.0
    command_failure_rate: float = 0.0
    python_failure_rate: float = 0.0
    verifier_quality: float = 1.0
    completion_quality: float = 1.0
    average_latency_ms: float = 0.0
    recovery_success_rate: float = 0.0
    weakness_score: float = 0.0
    updated_at: str = ""


class ApprovalEnvelope(BaseModel):
    """Bounded mutation recovery envelope created at confirmation time."""

    model_config = ConfigDict(extra="forbid")

    envelope_id: str
    request_id: str
    goal: str
    gateway_node: str = ""
    gateway_url: str = ""
    cwd_values: list[str] = Field(default_factory=list)
    action_ids: list[str] = Field(default_factory=list)
    max_risk: str = "medium"
    mutation_budget: int = 2
    used_mutations: int = 0
    status: Literal["active", "exhausted", "violated", "closed"] = "active"
    created_at: str = ""
    updated_at: str = ""


class ReliabilityEvalCase(BaseModel):
    """One deterministic reliability eval case."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    title: str
    failure_kind: FailureKind
    expected_recovery: RecoveryAction
    prompt: str


class ReliabilityEvalResult(BaseModel):
    """Stored eval run summary."""

    model_config = ConfigDict(extra="forbid")

    eval_id: str
    created_at: str
    total_cases: int
    recovered_cases: int
    blocked_cases: int
    failed_cases: int
    score: float
    cases: list[dict[str, Any]] = Field(default_factory=list)
