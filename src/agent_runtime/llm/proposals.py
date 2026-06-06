"""Untrusted proposal models and helpers for structured LLM planning stages."""

from __future__ import annotations

import json
from typing import Any, Generic, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError as PydanticValidationError,
    model_validator,
)

from agent_runtime.prompts import render_prompt

ModelT = TypeVar("ModelT", bound=BaseModel)


class PromptClassificationProposal(BaseModel):
    """Untrusted LLM proposal for prompt classification."""

    model_config = ConfigDict(extra="forbid")

    prompt_type: str
    requires_tools: bool
    likely_domains: list[str] = Field(default_factory=list)
    risk_level: str
    needs_clarification: bool
    clarification_question: str | None = None
    reason: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    assumptions: list[str] = Field(default_factory=list)
    domain_evaluations: list["DomainEvaluationProposal"] = Field(default_factory=list)


class PromptRephraseProposal(BaseModel):
    """Untrusted LLM proposal for non-authoritative prompt normalization."""

    model_config = ConfigDict(extra="forbid")

    rephrased_prompt: str
    changed: bool
    preserved_requirements: list[str] = Field(default_factory=list)
    removed_duplication: list[str] = Field(default_factory=list)
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)


class DomainEvaluationProposal(BaseModel):
    """Untrusted LLM proposal evaluating one declared runtime domain."""

    model_config = ConfigDict(extra="forbid")

    domain: str
    fits: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class DirectAnswerProposal(BaseModel):
    """Untrusted LLM proposal for direct no-tool answers."""

    model_config = ConfigDict(extra="forbid")

    answer: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reason: str = ""


class TaskFrameProposal(BaseModel):
    """Untrusted LLM proposal for one task frame."""

    model_config = ConfigDict(extra="forbid")

    id: str
    parent_id: str | None = None
    description: str
    semantic_verb: str = "unknown"
    object_type: str = "unknown"
    intent_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    constraints: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    raw_evidence: str | None = None
    requires_confirmation: bool = False
    risk_level: str = "low"
    operation_intent: str | None = None
    side_effect_type: str | None = None
    dependency_hints: list[str] = Field(default_factory=list)


class TaskDecompositionProposal(BaseModel):
    """Untrusted LLM proposal for prompt decomposition."""

    model_config = ConfigDict(extra="forbid")

    tasks: list[TaskFrameProposal] = Field(default_factory=list)
    global_constraints: dict[str, Any] = Field(default_factory=dict)
    unresolved_references: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class VerbAssignmentItemProposal(BaseModel):
    """Untrusted LLM proposal for one task verb assignment."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    semantic_verb: str
    object_type: str
    intent_confidence: float = Field(ge=0.0, le=1.0)
    risk_level: str
    requires_confirmation: bool = False


class VerbAssignmentProposal(BaseModel):
    """Untrusted LLM proposal for task verb assignment."""

    model_config = ConfigDict(extra="forbid")

    assignments: list[VerbAssignmentItemProposal] = Field(default_factory=list)


class CapabilityRefProposal(BaseModel):
    """Untrusted LLM proposal for one capability candidate."""

    model_config = ConfigDict(extra="forbid")

    capability_id: str
    operation_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    assumptions: list[str] = Field(default_factory=list)


CapabilityFitFailureMode = Literal[
    "semantic_mismatch",
    "domain_mismatch",
    "object_type_mismatch",
    "missing_required_arguments",
    "unsupported_capability_gap",
    "ambiguous",
    "rejected",
]


class CapabilityCandidateEvaluationProposal(BaseModel):
    """Untrusted LLM proposal evaluating one runtime-shortlisted capability."""

    model_config = ConfigDict(extra="forbid")

    capability_id: str
    operation_id: str
    fits: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    domain_reason: str = ""
    object_type_reason: str = ""
    argument_reason: str = ""
    risk_reason: str = ""
    missing_arguments_likely: list[str] = Field(default_factory=list)
    better_than_other_candidates: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_fit_fields(cls, values: Any) -> Any:
        """Accept older status-like capability judgments and normalize them."""

        if not isinstance(values, dict):
            return values
        normalized = dict(values)
        if (
            "missing_arguments_likely" not in normalized
            and "missing_arguments_likley" in normalized
        ):
            normalized["missing_arguments_likely"] = normalized.get("missing_arguments_likley")
        normalized.pop("missing_arguments_likley", None)
        if "fits" in normalized:
            return normalized
        status = str(normalized.get("proposed_status") or "").strip().lower()
        normalized["fits"] = status in {"fit", "fits", "compatible", "good_fit", "good_match"}
        if "reason" not in normalized:
            normalized["reason"] = (
                normalized.get("semantic_reason")
                or normalized.get("domain_reason")
                or normalized.get("object_type_reason")
                or ""
            )
        normalized.pop("proposed_status", None)
        return normalized


class CapabilitySelectionProposal(BaseModel):
    """Untrusted LLM proposal judging a deterministic shortlist of capabilities."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    evaluations: list[CapabilityCandidateEvaluationProposal] = Field(default_factory=list)
    unresolved_reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_selection_fields(cls, values: Any) -> Any:
        """Accept older candidate/selected payloads and project them into shortlist evaluations."""

        if not isinstance(values, dict):
            return values
        normalized = dict(values)
        if "evaluations" in normalized:
            return normalized
        candidates = normalized.get("candidates")
        selected = normalized.get("selected")
        selected_key: tuple[str, str] | None = None
        if isinstance(selected, dict):
            capability_id = str(selected.get("capability_id") or "").strip()
            operation_id = str(selected.get("operation_id") or "").strip()
            if capability_id and operation_id:
                selected_key = (capability_id, operation_id)
        evaluations: list[dict[str, Any]] = []
        if isinstance(candidates, list):
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                capability_id = str(candidate.get("capability_id") or "").strip()
                operation_id = str(candidate.get("operation_id") or "").strip()
                if not capability_id or not operation_id:
                    continue
                fits = selected_key == (capability_id, operation_id)
                reason = str(candidate.get("reason") or "").strip()
                evaluations.append(
                    {
                        "capability_id": capability_id,
                        "operation_id": operation_id,
                        "fits": fits,
                        "confidence": candidate.get("confidence", 0.0),
                        "reason": reason,
                        "domain_reason": reason,
                        "object_type_reason": reason,
                        "argument_reason": "",
                        "risk_reason": "",
                        "missing_arguments_likely": [],
                    }
                )
        normalized["evaluations"] = evaluations
        normalized.pop("candidates", None)
        normalized.pop("selected", None)
        return normalized


class CapabilityFitProposal(BaseModel):
    """Untrusted LLM proposal for whether a selected capability truly fits a task."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    candidate_capability_id: str
    candidate_operation_id: str
    fits: bool
    confidence: float = Field(ge=0.0, le=1.0)
    primary_failure_mode: CapabilityFitFailureMode | None = None
    semantic_reason: str = ""
    domain_reason: str = ""
    object_type_reason: str = ""
    argument_reason: str = ""
    risk_reason: str = ""
    better_capability_id: str | None = None
    missing_capability_description: str | None = None
    suggested_domain: str | None = None
    suggested_object_type: str | None = None
    missing_arguments_likely: list[str] = Field(default_factory=list)
    requires_clarification: bool = False
    clarification_question: str | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_status(cls, values: Any) -> Any:
        """Accept older free-form fit statuses and normalize them at the proposal boundary."""

        if not isinstance(values, dict):
            return values
        normalized = dict(values)
        if (
            "missing_arguments_likely" not in normalized
            and "missing_arguments_likley" in normalized
        ):
            normalized["missing_arguments_likely"] = normalized.get("missing_arguments_likley")
        normalized.pop("missing_arguments_likley", None)
        if "fits" in normalized:
            return normalized
        status = str(normalized.get("proposed_status") or "").strip().lower()
        positive = {"fit", "fits", "compatible", "good_fit", "good_match"}
        failure_modes = {
            "semantic_mismatch",
            "domain_mismatch",
            "object_type_mismatch",
            "missing_required_arguments",
            "unsupported_capability_gap",
            "ambiguous",
            "rejected",
        }
        normalized["fits"] = status in positive
        if "primary_failure_mode" not in normalized and status in failure_modes:
            normalized["primary_failure_mode"] = status
        normalized.pop("proposed_status", None)
        return normalized


class GeneratedArgumentProposal(BaseModel):
    """Untrusted LLM proposal for a runtime-generated argument value."""

    model_config = ConfigDict(extra="forbid")

    argument_name: str
    generator_id: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_generator_fields(cls, values: Any) -> Any:
        """Accept earlier generation_kind wording and normalize it."""

        if not isinstance(values, dict):
            return values
        normalized = dict(values)
        if "generator_id" not in normalized and "generation_kind" in normalized:
            normalized["generator_id"] = normalized.get("generation_kind")
        normalized.pop("generation_kind", None)
        return normalized


class ArgumentExtractionProposal(BaseModel):
    """Untrusted LLM proposal for capability arguments."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    capability_id: str
    operation_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    generated_arguments: list[GeneratedArgumentProposal] = Field(default_factory=list)
    missing_required_arguments: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class DAGReviewProposal(BaseModel):
    """Untrusted LLM proposal for DAG review."""

    model_config = ConfigDict(extra="forbid")

    missing_user_intents: list[str] = Field(default_factory=list)
    suspicious_nodes: list[str] = Field(default_factory=list)
    dependency_warnings: list[str] = Field(default_factory=list)
    dataflow_warnings: list[str] = Field(default_factory=list)
    output_expectation_warnings: list[str] = Field(default_factory=list)
    recommended_repair: dict[str, Any] | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class FailureRepairProposal(BaseModel):
    """Untrusted LLM proposal for post-failure repair."""

    model_config = ConfigDict(extra="forbid")

    failed_node_id: str | None = None
    proposed_action: Literal[
        "retry_with_arguments",
        "alternate_capability",
        "skip_with_explanation",
        "ask_user",
    ]
    corrected_arguments: dict[str, Any] = Field(default_factory=dict)
    alternate_capability_id: str | None = None
    user_message: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_fields(cls, values: Any) -> Any:
        """Accept older repair payload shapes and normalize them."""

        if not isinstance(values, dict):
            return values
        normalized = dict(values)
        if "proposed_action" not in normalized:
            strategy = str(normalized.get("strategy") or "").strip().lower()
            if strategy == "correct_arguments":
                normalized["proposed_action"] = "retry_with_arguments"
            elif strategy == "alternate_capability":
                normalized["proposed_action"] = "alternate_capability"
            elif normalized.get("skip_node"):
                normalized["proposed_action"] = "skip_with_explanation"
            elif normalized.get("ask_for_clarification"):
                normalized["proposed_action"] = "ask_user"
            else:
                normalized["proposed_action"] = strategy or "ask_user"
        action_aliases = {
            "repair": "retry_with_arguments",
            "retry": "retry_with_arguments",
            "correct_arguments": "retry_with_arguments",
        }
        proposed_action = str(normalized.get("proposed_action") or "").strip().lower()
        if proposed_action in action_aliases:
            normalized["proposed_action"] = action_aliases[proposed_action]
        if "user_message" not in normalized:
            normalized["user_message"] = (
                normalized.get("clarification_question")
                or normalized.get("explanation")
                or ""
            )
        if "reason" not in normalized:
            normalized["reason"] = str(
                normalized.get("explanation") or normalized.get("strategy") or ""
            ).strip()
        for legacy_key in (
            "strategy",
            "alternate_operation_id",
            "skip_node",
            "ask_for_clarification",
            "clarification_question",
            "explanation",
        ):
            normalized.pop(legacy_key, None)
        return normalized


class DisplayPlanProposal(BaseModel):
    """Untrusted LLM proposal for output display planning."""

    model_config = ConfigDict(extra="forbid")

    display_type: str
    title: str | None = None
    sections: list[dict[str, Any]] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    redaction_policy: str = "standard"


class DisplayPrimitiveEvaluationProposal(BaseModel):
    """Untrusted LLM proposal evaluating one declared display primitive/source pair."""

    model_config = ConfigDict(extra="forbid")

    primitive_id: str
    source_ref: str
    fits: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    title: str | None = None
    priority: int = 100
    parameters: dict[str, Any] = Field(default_factory=dict)


class DisplayPrimitiveSelectionProposal(BaseModel):
    """Untrusted LLM proposal judging fixed display primitive candidates."""

    model_config = ConfigDict(extra="forbid")

    evaluations: list[DisplayPrimitiveEvaluationProposal] = Field(default_factory=list)
    title: str | None = None
    include_raw_preview: bool = False


class StructuredProposalAttempt(BaseModel, Generic[ModelT]):
    """One raw/parsed structured proposal attempt."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    candidate_index: int
    raw_llm_response: Any = None
    parsed_proposal: Any = None
    validation_errors: list[str] = Field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Return whether the attempt parsed cleanly into the requested model."""

        return self.parsed_proposal is not None and not self.validation_errors


def _proposal_prompt(
    system_prompt: str,
    user_payload: Any,
    candidate_index: int,
    total_candidates: int,
) -> str:
    """Build one JSON-only prompt for an N-best structured proposal attempt."""

    payload = json.dumps(user_payload, sort_keys=True, default=str, ensure_ascii=True)
    return render_prompt(
        "llm.structured_proposal",
        {
            "system_prompt": system_prompt,
            "candidate_index": candidate_index + 1,
            "total_candidates": total_candidates,
            "payload_json": payload,
        },
    )


def collect_n_best_structured_attempts(
    llm_client,
    system_prompt: str,
    user_payload: Any,
    output_model: type[ModelT],
    n: int,
) -> list[StructuredProposalAttempt[ModelT]]:
    """Collect N-best raw/parsed structured proposal attempts."""

    total = max(1, int(n))
    schema = output_model.model_json_schema()
    attempts: list[StructuredProposalAttempt[ModelT]] = []
    for index in range(total):
        prompt = _proposal_prompt(system_prompt, user_payload, index, total)
        raw_response = llm_client.complete_json(prompt, schema)
        try:
            parsed = output_model.model_validate(raw_response)
            attempts.append(
                StructuredProposalAttempt[ModelT](
                    candidate_index=index,
                    raw_llm_response=raw_response,
                    parsed_proposal=parsed,
                )
            )
        except PydanticValidationError as exc:
            attempts.append(
                StructuredProposalAttempt[ModelT](
                    candidate_index=index,
                    raw_llm_response=raw_response,
                    validation_errors=[str(exc)],
                )
            )
    return attempts


def generate_n_best_structured_proposals(
    llm_client,
    system_prompt: str,
    user_payload: Any,
    output_model: type[ModelT],
    n: int,
) -> list[ModelT]:
    """Return the valid parsed structured proposals from N-best generation."""

    attempts = collect_n_best_structured_attempts(
        llm_client=llm_client,
        system_prompt=system_prompt,
        user_payload=user_payload,
        output_model=output_model,
        n=n,
    )
    proposals: list[ModelT] = []
    for attempt in attempts:
        if attempt.parsed_proposal is not None and not attempt.validation_errors:
            proposals.append(attempt.parsed_proposal)
    return proposals
