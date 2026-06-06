"""Typed contracts for the explicit Conversational pipeline."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


OperatorActionKind = Literal["shell_command", "python_transform", "python_action", "llm_text"]
OperatorExecutionMode = Literal["captured", "terminal_detached"]
OperatorInteractionMode = Literal["non_interactive", "may_prompt", "long_running"]
OperatorRiskLevel = Literal["low", "medium", "high", "critical"]
OperatorOutputShape = Literal["text", "json", "table", "file", "status", "interactive_stream"]
OperatorShellStdinMode = Literal["none", "literal", "input_binding"]
OperatorClarificationStrategy = Literal["llm_decides", "material_gaps", "ask_any_missing"]
OperatorEffectIntent = Literal["read_only", "mutates_state", "unknown"]
OperatorPolicyMode = Literal["deterministic", "llm"]
OperatorPolicyModule = Literal[
    "effect",
    "interaction",
    "stdout",
    "failure",
    "memory",
    "memory_question",
    "streaming_scope",
    "verb",
    "python_code_review",
]
OperatorPolicyDecisionName = Literal[
    "allow",
    "block",
    "repair",
    "ask_user",
    "unknown",
    "read_only",
    "mutates_state",
    "may_prompt",
    "long_running",
    "usable_stdout",
    "diagnostic_stdout",
    "applies",
    "not_applies",
    "satisfies",
    "leaks_future_task",
]
ValidationAdjudicationDecision = Literal[
    "allow_literal_payload",
    "repair_required",
    "block",
    "ask_user",
]
OperatorRepairAdjudicationDecision = Literal["accept", "revise_repair"]


def _empty_operator_literal(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


class OperatorTask(BaseModel):
    """One semantic task authored by the LLM in operator mode."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    goal: str
    semantic_verb: str
    object_type: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    reason: str = ""


class OperatorDependency(BaseModel):
    """Declared ordering or data dependency between operator actions."""

    model_config = ConfigDict(extra="forbid")

    producer_action_id: str
    consumer_action_id: str
    reason: str


class OperatorInputBinding(BaseModel):
    """Typed binding from a completed action output into a later action input."""

    model_config = ConfigDict(extra="forbid")

    input_name: str
    source_action_id: str
    source_field: Literal["stdout", "stderr", "exit_code", "output", "record"] = "stdout"
    required: bool = True
    fallback_value: Any = None

    @model_validator(mode="before")
    @classmethod
    def normalize_binding_shape(cls, values: Any) -> Any:
        """Accept one compact operator binding dialect and canonicalize it.

        The LLM sometimes describes the same binding as ``output_name`` or omits
        ``input_name`` when the intended input name is simply the source field.
        Keeping this normalization at the typed model boundary prevents the
        standard and explicit-operator paths from drifting.
        """

        if not isinstance(values, dict):
            return values
        normalized = dict(values)
        if "source_field" not in normalized:
            for alias in ("output_name", "output_key", "field"):
                alias_value = normalized.pop(alias, None)
                if alias_value:
                    normalized["source_field"] = alias_value
                    break
        else:
            for alias in ("output_name", "output_key", "field"):
                normalized.pop(alias, None)

        if "source_action_id" not in normalized:
            for alias in ("source_task_id", "source_node_id", "producer_action_id"):
                alias_value = normalized.pop(alias, None)
                if alias_value:
                    normalized["source_action_id"] = alias_value
                    break
        else:
            for alias in ("source_task_id", "source_node_id", "producer_action_id"):
                normalized.pop(alias, None)

        input_name = str(normalized.get("input_name") or "").strip()
        if not input_name:
            source_field = str(normalized.get("source_field") or "stdout").strip() or "stdout"
            normalized["input_name"] = source_field
        if "required" not in normalized:
            normalized["required"] = True
        return normalized


class OperatorAction(BaseModel):
    """One LLM-authored executable action."""

    model_config = ConfigDict(extra="forbid")

    action_id: str
    task_id: str
    kind: OperatorActionKind
    command: str | None = None
    code: str | None = None
    llm_prompt: str | None = None
    execution_mode: OperatorExecutionMode = "captured"
    interaction_mode: OperatorInteractionMode = Field(
        default="non_interactive",
        description=(
            "Whether this action is expected to run without user input, may prompt for "
            "terminal input, or is long-running terminal work."
        ),
    )
    cwd: str = "."
    inputs: dict[str, Any] = Field(default_factory=dict)
    input_bindings: list[OperatorInputBinding] = Field(default_factory=list)
    stdin_mode: OperatorShellStdinMode = "none"
    stdin_text: str | None = None
    stdin_input_name: str | None = None
    declared_output_shape: OperatorOutputShape = "text"
    defer_code_generation: bool = False
    allow_zero_result: bool = False
    risk: OperatorRiskLevel = "medium"
    effect_intent: OperatorEffectIntent = "unknown"
    effect_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    effect_summary: str = ""
    timeout_seconds: int | None = None
    reason: str
    depends_on: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_action_payload(self) -> "OperatorAction":
        """Keep shell and Python action payloads mutually exclusive."""

        if self.input_bindings and self.inputs:
            binding_names = {str(binding.input_name or "").strip() for binding in self.input_bindings}
            cleaned_inputs = {
                key: value
                for key, value in self.inputs.items()
                if key not in binding_names or not _empty_operator_literal(value)
            }
            if len(cleaned_inputs) != len(self.inputs):
                self.inputs = cleaned_inputs
        if self.kind == "shell_command":
            if not str(self.command or "").strip():
                raise ValueError("shell_command actions require command.")
            if self.code is not None:
                raise ValueError("shell_command actions must not include code.")
            if self.llm_prompt is not None:
                raise ValueError("shell_command actions must not include llm_prompt.")
            if self.stdin_mode == "literal" and self.stdin_text is None:
                raise ValueError("shell_command actions with stdin_mode literal require stdin_text.")
            if self.stdin_mode == "input_binding" and not str(self.stdin_input_name or "").strip():
                raise ValueError(
                    "shell_command actions with stdin_mode input_binding require stdin_input_name."
                )
            if self.stdin_mode == "none":
                if self.stdin_text is not None:
                    raise ValueError("shell_command actions with stdin_mode none must not include stdin_text.")
                if self.stdin_input_name is not None:
                    raise ValueError(
                        "shell_command actions with stdin_mode none must not include stdin_input_name."
                    )
        if self.kind in {"python_transform", "python_action"}:
            if not str(self.code or "").strip() and not self.defer_code_generation:
                raise ValueError(f"{self.kind} actions require code.")
            if self.command is not None:
                raise ValueError(f"{self.kind} actions must not include command.")
            if self.llm_prompt is not None:
                raise ValueError(f"{self.kind} actions must not include llm_prompt.")
            if self.stdin_mode != "none" or self.stdin_text is not None or self.stdin_input_name is not None:
                raise ValueError(f"{self.kind} actions must not include shell stdin fields.")
            if self.execution_mode != "captured":
                raise ValueError(f"{self.kind} actions support only captured execution_mode.")
            if self.interaction_mode != "non_interactive":
                raise ValueError(f"{self.kind} actions support only non_interactive interaction_mode.")
        if self.kind == "llm_text":
            if not str(self.llm_prompt or "").strip():
                raise ValueError("llm_text actions require llm_prompt.")
            if self.command is not None:
                raise ValueError("llm_text actions must not include command.")
            if self.code is not None:
                raise ValueError("llm_text actions must not include code.")
            if self.stdin_mode != "none" or self.stdin_text is not None or self.stdin_input_name is not None:
                raise ValueError("llm_text actions must not include shell stdin fields.")
            if self.execution_mode != "captured":
                raise ValueError("llm_text actions support only captured execution_mode.")
            if self.interaction_mode != "non_interactive":
                raise ValueError("llm_text actions support only non_interactive interaction_mode.")
            if self.defer_code_generation:
                raise ValueError("llm_text actions must not defer code generation.")
        return self


class OperatorPlan(BaseModel):
    """Complete LLM-authored operator plan."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    tasks: list[OperatorTask] = Field(default_factory=list)
    actions: list[OperatorAction] = Field(default_factory=list)
    dependencies: list[OperatorDependency] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class OperatorTryoutCapsule(BaseModel):
    """Retired compatibility capsule model."""

    model_config = ConfigDict(extra="forbid")

    task: OperatorTask
    action: OperatorAction
    dependencies: list[OperatorDependency] = Field(default_factory=list)
    expected_output: str = ""
    reason: str = ""


class OperatorTryoutCapsuleBatch(BaseModel):
    """Compact batch of operator-native capsules drafted before full planning."""

    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    capsules: list[OperatorTryoutCapsule] = Field(default_factory=list)
    goal_complete_if_successful: bool = False
    next_step_hint: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class OperatorTryoutResultReview(BaseModel):
    """Retired compatibility review model."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["complete", "repair_required", "fail"]
    evidence_summary: str = ""
    issues: list[str] = Field(default_factory=list)
    repair_feedback: str = ""
    next_step_hint: str = ""
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class OperatorPolicySubject(BaseModel):
    """One subject that a configurable operator policy module may judge."""

    model_config = ConfigDict(extra="forbid")

    module: OperatorPolicyModule
    subject_id: str
    task: dict[str, Any] = Field(default_factory=dict)
    action: dict[str, Any] = Field(default_factory=dict)
    plan: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class OperatorPolicyDecision(BaseModel):
    """Uniform typed decision returned by an operator policy module."""

    model_config = ConfigDict(extra="forbid")

    module: OperatorPolicyModule
    subject_id: str
    decision: OperatorPolicyDecisionName
    effect_intent: OperatorEffectIntent = "unknown"
    risk_level: OperatorRiskLevel = "medium"
    requires_confirmation: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    repair_hint: str = ""


class OperatorPolicyReviewRequest(BaseModel):
    """Batched policy review request passed to an LLM-owned policy module."""

    model_config = ConfigDict(extra="forbid")

    subjects: list[OperatorPolicySubject] = Field(default_factory=list)


class OperatorPolicyReviewResult(BaseModel):
    """Batched policy review result from an LLM-owned policy module call."""

    model_config = ConfigDict(extra="forbid")

    decisions: list[OperatorPolicyDecision] = Field(default_factory=list)


class ValidationAdjudication(BaseModel):
    """LLM adjudication for one context-sensitive validation failure."""

    model_config = ConfigDict(extra="forbid")

    decision: ValidationAdjudicationDecision
    validation_error: dict[str, Any] = Field(default_factory=dict)
    applies_to_action_id: str = ""
    literal_payload_evidence: list[str] = Field(default_factory=list)
    why_safe: str = ""
    repair_guidance: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class OperatorSelfQuestionAnswer(BaseModel):
    """One explicit question the LLM asks itself before planning, with its answer."""

    model_config = ConfigDict(extra="forbid")

    question: str
    answer: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class OperatorVerificationContract(BaseModel):
    """Typed success/failure semantics that the plan and review should satisfy."""

    model_config = ConfigDict(extra="forbid")

    goal: Literal[
        "target_absent",
        "target_present",
        "computed_answer",
        "mutation_succeeded",
        "listing_or_summary",
        "other",
    ]
    observable_state: str = Field(
        description="The concrete state, output, or answer that proves the request is complete."
    )
    success_when: str = Field(
        description="Condition under which verification should be considered successful."
    )
    failure_when: str = Field(
        description="Condition under which verification should be considered failed or incomplete."
    )
    freshness: Literal[
        "fresh_post_action_read",
        "same_action_verified",
        "captured_output_sufficient",
        "not_applicable",
    ] = "not_applicable"
    must_exit_nonzero_when_unmet: bool = Field(
        default=True,
        description="Whether an executable verifier should exit nonzero when failure_when is true.",
    )
    acceptable_evidence: list[str] = Field(default_factory=list)


class OperatorSelfBrief(BaseModel):
    """LLM-authored preflight brief used to improve operator planning and repair."""

    model_config = ConfigDict(extra="forbid")

    task_understanding: str
    self_questions: list[OperatorSelfQuestionAnswer] = Field(default_factory=list)
    key_domain_facts: list[str] = Field(default_factory=list)
    likely_pitfalls: list[str] = Field(default_factory=list)
    success_postcondition: str
    recommended_strategy: str
    verification_strategy: str
    verification_contract: OperatorVerificationContract
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class DeliberationFrame(BaseModel):
    """Concise guided reasoning frame used before planning."""

    model_config = ConfigDict(extra="forbid")

    user_goal: str
    success_criteria: list[str] = Field(default_factory=list)
    known_evidence: list[str] = Field(default_factory=list)
    unknowns_to_observe: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    candidate_strategies: list[str] = Field(default_factory=list)
    recommended_strategy: str
    why_this_strategy: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class PlanCritique(BaseModel):
    """LLM-authored guided critique of a candidate operator plan."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["accept", "repair_required", "ask_user", "block"]
    revised_plan: OperatorPlan | None = None
    clarification_request: "OperatorClarificationRequest | None" = None
    satisfies_user_goal: bool = True
    missing_evidence: list[str] = Field(default_factory=list)
    unnecessary_actions: list[str] = Field(default_factory=list)
    safer_alternative: str = ""
    repair_guidance: str = ""
    violated_memory_ids: list[str] = Field(default_factory=list)
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_plan_critique_decision(self) -> "PlanCritique":
        if self.decision == "ask_user" and self.clarification_request is None:
            raise ValueError("ask_user requires clarification_request.")
        return self


class CodeGenerationCritique(BaseModel):
    """LLM-authored guided critique of generated Python code."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["accept", "repair_required", "block"]
    revised_proposal: "OperatorPythonCodeProposal | None" = None
    contract_issues: list[str] = Field(default_factory=list)
    runtime_input_assumptions: list[str] = Field(default_factory=list)
    verification_gap: str = ""
    repair_guidance: str = ""
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_code_critique_decision(self) -> "CodeGenerationCritique":
        if self.decision == "repair_required" and self.revised_proposal is None:
            raise ValueError("repair_required requires revised_proposal.")
        return self


class EvidenceSatisfactionReview(BaseModel):
    """LLM-authored review of whether execution evidence satisfies the request."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["satisfied", "continue_required", "ask_user", "fail"]
    continuation_plan: OperatorPlan | None = None
    clarification_request: "OperatorClarificationRequest | None" = None
    evidence_summary: str
    unsatisfied_criteria: list[str] = Field(default_factory=list)
    next_step_guidance: str = ""
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_evidence_decision(self) -> "EvidenceSatisfactionReview":
        if self.decision == "continue_required" and self.continuation_plan is None:
            raise ValueError("continue_required requires continuation_plan.")
        if self.decision == "ask_user" and self.clarification_request is None:
            raise ValueError("ask_user requires clarification_request.")
        return self


class OperatorRepair(BaseModel):
    """LLM-authored repair response for one rejected operator plan."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["repair_plan", "ask_user"] = Field(
        default="repair_plan",
        description="Whether to return a corrected plan or pause for one user clarification.",
    )
    clarification_request: "OperatorClarificationRequest | None" = None
    root_cause: str = Field(
        description="Specific reason the prior plan/action failed, grounded in validation feedback or live execution records."
    )
    failed_postcondition: str = Field(
        description="The user-visible end state or structural contract that was not satisfied."
    )
    evidence_from_stdout_stderr: str = Field(
        description="Concrete evidence from validation feedback, stdout, stderr, traceback, or output previews."
    )
    required_change: str = Field(
        description="What must change in the repaired plan to address the root cause."
    )
    verification_strategy: str = Field(
        description="How the repaired plan will prove the request is complete, especially after mutations."
    )
    corrected_plan: OperatorPlan | None = None
    retry_rationale: str
    expected_change: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_repair_decision(self) -> "OperatorRepair":
        if self.decision == "repair_plan" and self.corrected_plan is None:
            raise ValueError("repair_plan requires corrected_plan.")
        if self.decision == "ask_user" and self.clarification_request is None:
            raise ValueError("ask_user requires clarification_request.")
        return self


class OperatorRepairAdjudication(BaseModel):
    """LLM-authored semantic review of one proposed execution repair."""

    model_config = ConfigDict(extra="forbid")

    decision: OperatorRepairAdjudicationDecision = Field(
        description="Whether the proposed repair should continue to deterministic validation or be revised."
    )
    issues: list[str] = Field(
        default_factory=list,
        description="Concrete trace-grounded issues in the proposed repair, empty when accepted.",
    )
    required_change: str = Field(
        default="",
        description="The specific change the next repair attempt must make when revision is required.",
    )
    reason: str = Field(
        default="",
        description="Short explanation grounded in the failed command, stderr/stdout, and proposed repair.",
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_adjudication_decision(self) -> "OperatorRepairAdjudication":
        if self.decision == "revise_repair" and not str(self.required_change or "").strip():
            raise ValueError("revise_repair requires required_change.")
        return self


class OperatorFailureContinuationReview(BaseModel):
    """LLM-authored decision for continuing after a partially failed operator run."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal[
        "continue_remaining",
        "repair_failed_action",
        "run_reconciliation_probe",
        "ask_user",
        "block",
        "already_complete",
    ]
    failed_action_id: str = ""
    skip_action_ids: list[str] = Field(default_factory=list)
    read_only_probe_plan: "OperatorPlan | None" = None
    corrected_plan: "OperatorPlan | None" = None
    clarification_request: "OperatorClarificationRequest | None" = None
    state_reconciliation: str = Field(
        default="",
        description="How the continuation accounts for side effects from the failed action.",
    )
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_continuation_decision(self) -> "OperatorFailureContinuationReview":
        if self.decision == "run_reconciliation_probe" and self.read_only_probe_plan is None:
            raise ValueError("run_reconciliation_probe requires read_only_probe_plan.")
        if self.decision in {"continue_remaining", "repair_failed_action"} and self.corrected_plan is None:
            raise ValueError("continue/repair decisions require corrected_plan.")
        if self.decision == "ask_user" and self.clarification_request is None:
            raise ValueError("ask_user requires clarification_request.")
        return self


class OperatorPlanReview(BaseModel):
    """LLM-authored quality review for one validated operator plan."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["accept", "revise"]
    revised_plan: OperatorPlan | None = None
    strategy_alignment: str = Field(
        description="Whether the plan follows the operator self-brief recommended strategy and why."
    )
    postcondition_alignment: str = Field(
        description="Whether the plan proves the self-brief success_postcondition or user end state."
    )
    verification_contract_alignment: str = Field(
        description="Whether the plan satisfies the typed self-brief verification_contract."
    )
    verification_gap: str = Field(
        description="Any verification weakness, especially stale data or wrong success/exit semantics; use 'none' if no gap."
    )
    required_revision: str = Field(
        description="Concrete change required before execution; use 'none' when accepting."
    )
    issues: list[str] = Field(default_factory=list)
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class MemoryComplianceReview(BaseModel):
    """LLM-authored review of whether a plan follows applied memory directives."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["accept", "repair_required", "ignore_memory"]
    revised_plan: OperatorPlan | None = None
    violated_memory_ids: list[str] = Field(default_factory=list)
    repair_guidance: str = Field(
        default="",
        description="Concrete changes needed to satisfy memory; use 'none' when accepting.",
    )
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_memory_compliance_decision(self) -> "MemoryComplianceReview":
        if self.decision == "repair_required" and self.revised_plan is None:
            raise ValueError("repair_required requires revised_plan.")
        return self


class OperatorCompletionReview(BaseModel):
    """LLM-authored review of whether execution satisfied the user's end state."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["complete", "needs_more_actions", "ask_user"]
    continuation_plan: OperatorPlan | None = None
    clarification_request: "OperatorClarificationRequest | None" = None
    issues: list[str] = Field(default_factory=list)
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_completion_decision(self) -> "OperatorCompletionReview":
        if self.decision == "needs_more_actions" and self.continuation_plan is None:
            raise ValueError("needs_more_actions requires continuation_plan.")
        if self.decision == "ask_user" and self.clarification_request is None:
            raise ValueError("ask_user requires clarification_request.")
        return self


class OperatorStepValidationContract(BaseModel):
    """Runtime-authored contract for one decomposed/streaming step."""

    model_config = ConfigDict(extra="forbid")

    original_request: str = ""
    step_id: str = ""
    task_id: str = ""
    step_index: int = 0
    step_description: str = ""
    semantic_verb: str = ""
    object_type: str = ""
    exact_requested_outputs: list[str] = Field(default_factory=list)
    requested_artifacts: list[str] = Field(default_factory=list)
    requested_values: list[str] = Field(default_factory=list)
    requested_mutations: list[str] = Field(default_factory=list)
    success_conditions: list[str] = Field(default_factory=list)
    evidence_expectations: list[str] = Field(default_factory=list)


class OperatorStepValidationReview(BaseModel):
    """LLM-owned judgment of whether one step satisfied its exact contract."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["accept", "repair", "continue_with_more_evidence", "block"]
    satisfied: bool = False
    evidence_summary: str = ""
    missing_evidence: list[str] = Field(default_factory=list)
    contract_violations: list[str] = Field(default_factory=list)
    repair_guidance: str = ""
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class OperatorClarificationOption(BaseModel):
    """Legacy suggested answer option for a user-facing clarification."""

    model_config = ConfigDict(extra="forbid")

    option_id: str
    label: str
    description: str = ""


class OperatorParameterClarificationChoice(BaseModel):
    """Masked Parameter Store option attached to credential clarifications."""

    model_config = ConfigDict(extra="forbid")

    choice_id: str
    key: str
    normalized_key: str
    label: str = ""
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    sensitive: bool = True
    field_paths: list[str] = Field(default_factory=list)
    env_names: list[str] = Field(default_factory=list)
    exact: bool = False
    score: int = 0
    match_reasons: list[str] = Field(default_factory=list)
    group: Literal["relevant", "all"] = "relevant"


class OperatorClarificationRequest(BaseModel):
    """One user-facing question the runtime should ask before planning or repair."""

    model_config = ConfigDict(extra="forbid")

    question: str
    reason: str
    missing_information: str
    options: list[OperatorClarificationOption] = Field(default_factory=list, max_length=3)
    input_kind: Literal[
        "password",
        "passphrase",
        "private_key",
        "token",
        "credential",
        "unknown",
    ] = "unknown"
    parameter_choices: list[OperatorParameterClarificationChoice] = Field(
        default_factory=list,
        max_length=20,
    )
    secret_input: bool = False
    allow_freeform: bool = True
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorClarificationDecision(BaseModel):
    """LLM decision to continue or pause for one clarification question."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["continue", "ask_user"]
    clarification_request: OperatorClarificationRequest | None = None
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_clarification_decision(self) -> "OperatorClarificationDecision":
        if self.decision == "ask_user" and self.clarification_request is None:
            raise ValueError("ask_user requires clarification_request.")
        return self


class OperatorFollowupDecision(BaseModel):
    """LLM-authored decision for one conversational operator follow-up."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["answer_from_context", "plan_actions"]
    answer: str = ""
    plan: OperatorPlan | None = None
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class OperatorFinalFormatter(BaseModel):
    """LLM-authored pure Python formatter for final user-facing output."""

    model_config = ConfigDict(extra="forbid")

    code: str
    declared_output_shape: Literal["markdown", "text", "json"] = "markdown"
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class OperatorAnswerJudge(BaseModel):
    """LLM-authored judgment of whether a formatted answer satisfies the user."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["accept", "revise_formatter"]
    answers_user_request: bool
    uses_available_outputs: bool
    issues: list[str] = Field(default_factory=list)
    feedback_for_formatter: str = ""
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class OperatorCardinalityJudge(BaseModel):
    """LLM-authored judgment of final-answer cardinality obligations."""

    model_config = ConfigDict(extra="forbid")

    applies: StrictBool = False
    verdict: Literal["accept", "revise"]
    issues: list[str] = Field(default_factory=list)
    required_change: str = ""
    feedback_for_formatter: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_cardinality_judgment(self) -> "OperatorCardinalityJudge":
        if not self.applies and self.verdict != "accept":
            raise ValueError("verdict must be accept when applies is false.")
        if self.verdict == "revise" and not (
            self.feedback_for_formatter.strip() or self.required_change.strip()
        ):
            raise ValueError("revise requires feedback_for_formatter or required_change.")
        return self


class OperatorPythonCodeProposal(BaseModel):
    """LLM-authored Python code generated after upstream inputs are known."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        description=(
            "One complete Python function string. For python_action it must start exactly "
            "with def main(inputs):. For python_transform it must start exactly with "
            "def transform(inputs):. All imports and executable work must be inside the function body."
        )
    )
    declared_output_shape: OperatorOutputShape = "text"
    allow_zero_result: bool = False
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class OperatorPythonCodeReview(BaseModel):
    """LLM-authored review for generated deferred Python code."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["accept", "revise"]
    revised_proposal: OperatorPythonCodeProposal | None = None
    issues: list[str] = Field(default_factory=list)
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class GeneratedPythonProofResult(BaseModel):
    """Internal deterministic proof result for generated Python code."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["accept", "reject", "skip_execution"]
    reason: str = ""
    proof_mode: Literal["static_only", "pure_runtime_sample"] = "static_only"
    failure_signature: str = ""
    code_hash: str = ""
    input_names: list[str] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    stdout_preview: str = ""
    stderr_preview: str = ""


class OperatorPlanCacheDecision(BaseModel):
    """LLM decision for adapting a private cached operator plan."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["use_adapted_plan", "fallback_to_normal_planning", "ask_user"]
    adapted_plan: OperatorPlan | None = None
    clarification_request: OperatorClarificationRequest | None = None
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_decision_payload(self) -> "OperatorPlanCacheDecision":
        if self.decision == "use_adapted_plan" and self.adapted_plan is None:
            raise ValueError("use_adapted_plan requires adapted_plan")
        if self.decision == "ask_user" and self.clarification_request is None:
            raise ValueError("ask_user requires clarification_request")
        return self


class OperatorCommandTemplateVariable(BaseModel):
    """One variable in a learned shell command template."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    observed_value: str = ""

    @model_validator(mode="after")
    def validate_name(self) -> "OperatorCommandTemplateVariable":
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", str(self.name or "")):
            raise ValueError("template variable names must be lowercase snake_case")
        return self


class OperatorCommandTemplateDraft(BaseModel):
    """LLM draft for learning a reusable shell command template."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["store_template", "skip_learning"]
    command_template: str = ""
    variables: list[OperatorCommandTemplateVariable] = Field(default_factory=list)
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_decision_payload(self) -> "OperatorCommandTemplateDraft":
        if self.decision == "store_template" and not str(self.command_template or "").strip():
            raise ValueError("store_template requires command_template")
        return self


class OperatorCommandTemplateDecision(BaseModel):
    """LLM decision for reusing one learned command template."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["use_template", "fallback_to_planning"]
    selected_template_id: str = ""
    variables: dict[str, str] = Field(default_factory=dict)
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_decision_payload(self) -> "OperatorCommandTemplateDecision":
        if self.decision == "use_template" and not str(self.selected_template_id or "").strip():
            raise ValueError("use_template requires selected_template_id")
        for name in self.variables:
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", str(name or "")):
                raise ValueError("template variable names must be lowercase snake_case")
        return self


class OperatorComputationCacheDecision(BaseModel):
    """LLM decision for adapting one cached computation-code template."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["use_template", "adapt_template", "fallback_to_fresh_code", "ask_user"]
    proposal: OperatorPythonCodeProposal | None = None
    clarification_request: OperatorClarificationRequest | None = None
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_decision_payload(self) -> "OperatorComputationCacheDecision":
        if self.decision in {"use_template", "adapt_template"} and self.proposal is None:
            raise ValueError("use_template/adapt_template requires proposal")
        if self.decision == "ask_user" and self.clarification_request is None:
            raise ValueError("ask_user requires clarification_request")
        return self


class OperatorExecutionRecord(BaseModel):
    """Execution result for one operator action."""

    model_config = ConfigDict(extra="forbid")

    action_id: str
    task_id: str
    kind: OperatorActionKind
    status: Literal["success", "error"]
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    output: Any = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorTextGenerationDraft(BaseModel):
    """Compact generated-text result from runtime evidence."""

    model_config = ConfigDict(extra="forbid")

    text: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ShellStdoutErrorJudge(BaseModel):
    """Strict yes/no judgment for ambiguous non-zero shell stdout."""

    model_config = ConfigDict(extra="forbid")

    is_error: StrictBool
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class SudoRetryJudge(BaseModel):
    """Strict judgment for whether sudo should repair a failed shell command."""

    model_config = ConfigDict(extra="forbid")

    is_permission_denied: StrictBool
    sudo_required_or_likely_to_fix: StrictBool
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class OperatorPipelineResult(BaseModel):
    """Final operator pipeline envelope returned to the orchestrator."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["success", "error", "confirmation_required", "clarification_required"]
    final_response: str
    plan: OperatorPlan | None = None
    execution_records: list[OperatorExecutionRecord] = Field(default_factory=list)
    confirmation_required: bool = False
    confirmation_actions: list[dict[str, Any]] = Field(default_factory=list)
    clarification_required: bool = False
    clarification_request: OperatorClarificationRequest | None = None
    display_document: dict[str, Any] | None = None
    validation_errors: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorRephraseRetryProposal(BaseModel):
    """One-shot equivalent prompt rewrite used after planning validation fails."""

    model_config = ConfigDict(extra="forbid")

    rephrased_prompt: str = Field(min_length=1)
    reason: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


__all__ = [
    "OperatorAction",
    "OperatorAnswerJudge",
    "OperatorClarificationDecision",
    "OperatorClarificationOption",
    "OperatorClarificationRequest",
    "OperatorParameterClarificationChoice",
    "OperatorClarificationStrategy",
    "OperatorCompletionReview",
    "OperatorStepValidationContract",
    "OperatorStepValidationReview",
    "CodeGenerationCritique",
    "DeliberationFrame",
    "OperatorDependency",
    "EvidenceSatisfactionReview",
    "OperatorExecutionRecord",
    "OperatorFailureContinuationReview",
    "OperatorExecutionMode",
    "OperatorEffectIntent",
    "OperatorInteractionMode",
    "OperatorFinalFormatter",
    "OperatorFollowupDecision",
    "OperatorInputBinding",
    "OperatorShellStdinMode",
    "OperatorTextGenerationDraft",
    "OperatorPipelineResult",
    "OperatorPolicyDecision",
    "OperatorPolicyDecisionName",
    "OperatorPolicyMode",
    "OperatorPolicyModule",
    "OperatorPolicyReviewRequest",
    "OperatorPolicyReviewResult",
    "OperatorPolicySubject",
    "PlanCritique",
    "OperatorCommandTemplateDecision",
    "OperatorCommandTemplateDraft",
    "OperatorCommandTemplateVariable",
    "OperatorPlanCacheDecision",
    "OperatorPlan",
    "OperatorPlanReview",
    "OperatorPythonCodeProposal",
    "OperatorPythonCodeReview",
    "GeneratedPythonProofResult",
    "OperatorRepair",
    "OperatorRepairAdjudication",
    "OperatorRephraseRetryProposal",
    "OperatorSelfQuestionAnswer",
    "OperatorSelfBrief",
    "OperatorVerificationContract",
    "ShellStdoutErrorJudge",
    "SudoRetryJudge",
    "OperatorTask",
    "OperatorTryoutCapsule",
    "OperatorTryoutCapsuleBatch",
    "OperatorTryoutResultReview",
    "ValidationAdjudication",
]
