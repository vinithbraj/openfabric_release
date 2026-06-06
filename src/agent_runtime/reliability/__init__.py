"""Capability Reliability Kernel public API."""

from agent_runtime.reliability.controller import (
    ReliabilityController,
    classify_failure,
    model_id_from_client,
)
from agent_runtime.reliability.evals import (
    DEFAULT_RELIABILITY_EVAL_CASES,
    run_reliability_eval,
)
from agent_runtime.reliability.repair_budget_benchmarks import recommend_execution_repair_budget
from agent_runtime.reliability.models import (
    ApprovalEnvelope,
    AnswerCoverageReview,
    AnswerObligationSet,
    CoverageVerdict,
    EvidenceObligation,
    EvidenceSensitivity,
    FailureKind,
    ModelCapabilityProfile,
    ObligationStatus,
    OutcomeVerification,
    RecoveryAction,
    RecoveryBudget,
    RecoveryDecision,
    ReliabilityEventKind,
    ReliabilityEvalCase,
    ReliabilityEvalResult,
    ReliabilityEvent,
    ReliabilityMode,
    VerificationStatus,
)
from agent_runtime.reliability.store import AgentReliabilityStore

__all__ = [
    "AgentReliabilityStore",
    "ApprovalEnvelope",
    "AnswerCoverageReview",
    "AnswerObligationSet",
    "CoverageVerdict",
    "DEFAULT_RELIABILITY_EVAL_CASES",
    "EvidenceObligation",
    "EvidenceSensitivity",
    "FailureKind",
    "ModelCapabilityProfile",
    "ObligationStatus",
    "OutcomeVerification",
    "RecoveryAction",
    "RecoveryBudget",
    "RecoveryDecision",
    "ReliabilityEventKind",
    "ReliabilityController",
    "ReliabilityEvalCase",
    "ReliabilityEvalResult",
    "ReliabilityEvent",
    "ReliabilityMode",
    "VerificationStatus",
    "classify_failure",
    "model_id_from_client",
    "run_reliability_eval",
    "recommend_execution_repair_budget",
]
