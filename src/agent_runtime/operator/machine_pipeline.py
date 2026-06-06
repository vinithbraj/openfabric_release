"""Compact machine-oriented operator pipeline.

This module is intentionally separate from ``operator.pipeline``.  The verbose
pipeline keeps its existing prompts and schemas; this module owns the compact
contracts used when ``llm_operator_verbose_enabled`` is false.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.capabilities import CapabilityRegistry
from agent_runtime.capabilities.sql import sql_prompt_lines_from_context
from agent_runtime.core.types import ALLOWED_SEMANTIC_VERBS, TaskFrame, UserRequest
from agent_runtime.input_pipeline.decomposition import (
    DecompositionResult,
    PromptClassification,
    _allowed_domains,
    _prompt_requires_runtime_observation,
    _sanitize_decomposition_for_stage_boundary,
)
from agent_runtime.input_pipeline.validators import (
    DECOMPOSITION_FORBIDDEN_CONSTRAINT_KEYS,
    PlanningContractValidator,
)
from agent_runtime.llm.reproducibility import PlanningTrace, PlanningTraceEntry, append_trace_entry, llm_client_metadata
from agent_runtime.llm.structured_call import structured_call
from agent_runtime.memory import memory_prompt_lines_from_context
from agent_runtime.parameters import parameter_prompt_lines_from_context
from agent_runtime.prompts import prompt_lines
from agent_runtime.settings_consolidation import (
    operator_legacy_override,
    reasoning_settings_from_profile,
)
from agent_runtime.operator.models import (
    OperatorPlan,
    OperatorSelfBrief,
    OperatorVerificationContract,
)
from agent_runtime.operator.pipeline import (
    LLMOperatorPipeline,
    OPERATOR_MEMORY_COMPLIANCE_REJECTED,
    OPERATOR_MEMORY_COMPLIANCE_REPAIRED,
    OPERATOR_MEMORY_COMPLIANCE_REVIEWED,
    OPERATOR_PLAN_REVIEW_ACCEPTED,
    OPERATOR_PLAN_REVIEW_PROPOSED,
    OPERATOR_PLAN_REVIEW_REJECTED,
    OperatorValidationError,
    _memory_constraint_errors,
    _memory_directives_for_current_intent,
    _memory_directives_for_stage,
    _memory_repair_preservation_errors,
    _operator_absolute_path_lines,
    _operator_clarification_lines,
    _operator_contract_lines,
    _operator_policy_note_lines,
    _operator_prompt_intent_block,
    _operator_literal_payload_lines,
    _operator_self_brief_from_request,
    _operator_streaming_step_lines,
    _operator_online_lookup_lines,
    _operator_user_macro_lines,
    _operator_verification_enforced_from_request,
    _shell_input_bindings_mode_from_request,
    _stable_json,
    _terminal_cwd_from_request,
    _terminal_execution_enabled_from_request,
    memory_guard_rule_summaries,
)


class MachineClassificationProposal(BaseModel):
    """Compact machine classification for routing only."""

    model_config = ConfigDict(extra="ignore")

    prompt_type: Literal[
        "simple_question",
        "simple_tool_task",
        "compound_tool_task",
        "complex_workflow",
        "ambiguous",
        "unsupported",
    ]
    requires_tools: bool
    likely_domains: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high", "critical"]
    needs_clarification: bool = False
    clarification_question: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class MachineTaskProposal(BaseModel):
    """Compact task frame proposal."""

    model_config = ConfigDict(extra="ignore")

    id: str
    description: str
    semantic_verb: str = "unknown"
    object_type: str = "unknown"
    dependencies: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False
    risk_level: Literal["low", "medium", "high", "critical"] = "low"
    constraints: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Semantic filters only. Never include downstream execution fields such "
            "as command, code, shell_command, cwd, inputs, input_bindings, "
            "arguments, operation_id, capability_id, sql/query, output keys, or "
            "timeouts."
        ),
    )
    operation_intent: str | None = None
    side_effect_type: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class MachineDecompositionProposal(BaseModel):
    """Compact decomposition proposal."""

    model_config = ConfigDict(extra="ignore")

    tasks: list[MachineTaskProposal] = Field(default_factory=list)
    global_constraints: dict[str, Any] = Field(default_factory=dict)
    unresolved_references: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class MachineSelfBrief(BaseModel):
    """Compact self-brief used as machine planning guidance."""

    model_config = ConfigDict(extra="forbid")

    goal: str
    strategy: str
    success_when: str
    failure_when: str
    verification_goal: Literal[
        "target_absent",
        "target_present",
        "computed_answer",
        "mutation_succeeded",
        "listing_or_summary",
        "other",
    ] = "other"
    freshness: Literal[
        "fresh_post_action_read",
        "same_action_verified",
        "captured_output_sufficient",
        "not_applicable",
    ] = "not_applicable"
    pitfalls: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class MachinePlanAudit(BaseModel):
    """Compact plan audit. It never returns a replacement plan."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["accept", "repair_required", "block"]
    issues: list[str] = Field(default_factory=list)
    repair_guidance: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class MachineMemoryComplianceAudit(BaseModel):
    """Compact memory compliance audit."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["accept", "repair_required", "ignore_memory"]
    violated_memory_ids: list[str] = Field(default_factory=list)
    repair_guidance: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


def _registry_domains(registry: CapabilityRegistry | None) -> list[str]:
    return _allowed_domains(registry)


def _classification_prompt(user_request: UserRequest, registry: CapabilityRegistry | None) -> str:
    domains = _registry_domains(registry)
    return "\n".join(
        [
            *prompt_lines("operator.machine_classification"),
            "You are classifying a user prompt for routing.",
            *sql_prompt_lines_from_context(user_request, stage="classification"),
            "Use only these domains:",
            _stable_json(domains),
            "Schema:",
            _stable_json(MachineClassificationProposal.model_json_schema()),
            "Prompt:",
            user_request.raw_prompt,
        ]
    )


def classify_prompt_compact(
    user_request: UserRequest,
    llm_client: Any,
    registry: CapabilityRegistry | None = None,
) -> PromptClassification:
    """Classify through the compact machine contract."""

    proposal = structured_call(
        llm_client,
        _classification_prompt(user_request, registry),
        MachineClassificationProposal,
    )
    allowed = _registry_domains(registry)
    allowed_map = {domain.lower(): domain for domain in allowed}
    domains = [
        allowed_map.get(str(domain).strip().lower(), str(domain).strip())
        for domain in proposal.likely_domains
        if str(domain or "").strip()
        and (not allowed_map or str(domain).strip().lower() in allowed_map)
    ]
    requires_tools = bool(proposal.requires_tools)
    if domains:
        requires_tools = True
    if not requires_tools and _prompt_requires_runtime_observation(user_request.raw_prompt):
        requires_tools = True
        if "operator" in allowed_map:
            domains = ["operator"]
    prompt_type = proposal.prompt_type
    if requires_tools and prompt_type == "simple_question":
        prompt_type = "simple_tool_task"
    if not requires_tools:
        domains = []
    classification = PromptClassification.model_validate(
        {
            "prompt_type": prompt_type,
            "requires_tools": requires_tools,
            "likely_domains": list(dict.fromkeys(domains)),
            "risk_level": proposal.risk_level,
            "needs_clarification": proposal.needs_clarification,
            "clarification_question": proposal.clarification_question,
            "reason": "compact",
        }
    )
    trace = user_request.safety_context.get("planning_trace")
    if isinstance(trace, PlanningTrace):
        model_name, temperature = llm_client_metadata(llm_client)
        append_trace_entry(
            trace,
            PlanningTraceEntry(
                stage="prompt_classification",
                request_id=user_request.request_id,
                model_name=model_name,
                llm_temperature=temperature,
                prompt_template_id="machine_prompt_classification",
                raw_llm_response=proposal.model_dump(mode="json"),
                parsed_proposal=proposal.model_dump(mode="json"),
                selected_candidate=classification.model_dump(mode="json"),
                deterministic_normalizations=["llm_contract_mode=compact"],
            ),
        )
    return classification


def _decomposition_prompt(
    user_request: UserRequest,
    classification: PromptClassification,
    registry: CapabilityRegistry | None,
) -> str:
    verbs = list(ALLOWED_SEMANTIC_VERBS)
    domains = _registry_domains(registry)
    return "\n".join(
        [
            *prompt_lines("operator.machine_decomposition"),
            "You are decomposing a user prompt into ordered semantic tasks.",
            *sql_prompt_lines_from_context(user_request, stage="decomposition"),
            "Forbidden constraint keys:",
            _stable_json(sorted(DECOMPOSITION_FORBIDDEN_CONSTRAINT_KEYS)),
            "Example: user says 'docker compose up' -> task intent 'start compose services', not constraints.command.",
            "Treat [provided ... payload] placeholders as literal data for a task, not task text.",
            "For troubleshooting, recovery, or fix requests, do not invent destructive storage reset tasks. Formatting, wiping, erasing, repartitioning, or creating a new filesystem is valid only when the user explicitly requested that data-destructive reset; otherwise decompose into inspect, diagnose, repair, or ask-user tasks.",
            "Allowed semantic verbs:",
            _stable_json(verbs),
            "Domains:",
            _stable_json(domains),
            "Classification:",
            _stable_json(classification.model_dump(mode="json")),
            "Schema:",
            _stable_json(MachineDecompositionProposal.model_json_schema()),
            "Prompt:",
            user_request.raw_prompt,
        ]
    )


def _decomposition_from_machine(
    proposal: MachineDecompositionProposal,
    *,
    original_prompt: str,
) -> DecompositionResult:
    tasks: list[TaskFrame] = []
    for item in proposal.tasks:
        verb = item.semantic_verb if item.semantic_verb in ALLOWED_SEMANTIC_VERBS else "unknown"
        tasks.append(
            TaskFrame(
                id=item.id,
                description=item.description,
                semantic_verb=verb,
                object_type=item.object_type or "unknown",
                intent_confidence=item.confidence,
                constraints=dict(item.constraints),
                dependencies=list(item.dependencies),
                raw_evidence=None,
                requires_confirmation=item.requires_confirmation,
                risk_level=item.risk_level,
                operation_intent=item.operation_intent,
                side_effect_type=item.side_effect_type,
                dependency_hints=[],
            )
        )
    decomposition = DecompositionResult(
        tasks=tasks,
        global_constraints=dict(proposal.global_constraints),
        unresolved_references=list(proposal.unresolved_references),
        assumptions=[],
    )
    decomposition, _ = _sanitize_decomposition_for_stage_boundary(
        decomposition,
        original_prompt=original_prompt,
    )
    validation = PlanningContractValidator().validate_tasks(
        decomposition.tasks,
        original_prompt=original_prompt,
    )
    if not validation.accepted:
        raise ValueError(
            "Compact decomposition failed validation: "
            + "; ".join(issue.message for issue in validation.issues)
        )
    return decomposition


def decompose_prompt_compact(
    user_request: UserRequest,
    classification: PromptClassification,
    llm_client: Any,
    available_domains: list[str] | None = None,
    registry: CapabilityRegistry | None = None,
) -> DecompositionResult:
    """Decompose through the compact machine contract."""

    del available_domains
    proposal = structured_call(
        llm_client,
        _decomposition_prompt(user_request, classification, registry),
        MachineDecompositionProposal,
    )
    decomposition = _decomposition_from_machine(proposal, original_prompt=user_request.raw_prompt)
    trace = user_request.safety_context.get("planning_trace")
    if isinstance(trace, PlanningTrace):
        model_name, temperature = llm_client_metadata(llm_client)
        append_trace_entry(
            trace,
            PlanningTraceEntry(
                stage="task_decomposition",
                request_id=user_request.request_id,
                model_name=model_name,
                llm_temperature=temperature,
                prompt_template_id="machine_task_decomposition",
                raw_llm_response=proposal.model_dump(mode="json"),
                parsed_proposal=proposal.model_dump(mode="json"),
                selected_candidate=decomposition.model_dump(mode="json"),
                deterministic_normalizations=["llm_contract_mode=compact"],
            ),
        )
    return decomposition


def _machine_self_brief_prompt(
    user_request: UserRequest,
    conversation_context: dict[str, Any] | None = None,
) -> str:
    context = dict(user_request.session_context or {})
    current_task = context.get("operator_streaming_current_task")
    scope = current_task if isinstance(current_task, dict) else {"prompt": user_request.raw_prompt}
    return "\n".join(
        [
            "Return compact machine self-brief JSON only.",
            "No prose beyond field values. No commands unless they are user-named facts.",
            "Focus only on current streaming task when present.",
            *_operator_streaming_step_lines(user_request),
            *_operator_clarification_lines(user_request),
            *_operator_policy_note_lines(user_request),
            *_operator_user_macro_lines(user_request),
            *_operator_online_lookup_lines(user_request),
            *_operator_literal_payload_lines(user_request),
            *_operator_absolute_path_lines(),
            "Schema:",
            _stable_json(MachineSelfBrief.model_json_schema()),
            "Scope:",
            _stable_json(scope),
            "Conversation:",
            _stable_json(conversation_context or {}),
            "Prompt:",
            user_request.raw_prompt,
        ]
    )


def _operator_self_brief_from_machine(brief: MachineSelfBrief) -> OperatorSelfBrief:
    contract = OperatorVerificationContract(
        goal=brief.verification_goal,
        observable_state=brief.success_when,
        success_when=brief.success_when,
        failure_when=brief.failure_when,
        freshness=brief.freshness,
        must_exit_nonzero_when_unmet=True,
        acceptable_evidence=["Captured runtime output or fresh state evidence."],
    )
    return OperatorSelfBrief(
        task_understanding=brief.goal,
        self_questions=[],
        key_domain_facts=list(brief.facts),
        likely_pitfalls=list(brief.pitfalls),
        success_postcondition=brief.success_when,
        recommended_strategy=brief.strategy,
        verification_strategy=brief.success_when,
        verification_contract=contract,
        confidence=brief.confidence,
    )


def _machine_plan_prompt(user_request: UserRequest, feedback: list[dict[str, Any]] | None = None) -> str:
    intent_block = user_request.session_context.get("operator_intent_block")
    verification_enforced = _operator_verification_enforced_from_request(user_request)
    lines = [
        *prompt_lines("operator.machine_plan"),
        *memory_prompt_lines_from_context(user_request, stage="operator_plan"),
        *parameter_prompt_lines_from_context(user_request, stage="operator_plan"),
        *_operator_clarification_lines(user_request),
        *_operator_policy_note_lines(user_request),
        *_operator_streaming_step_lines(user_request),
        *_operator_user_macro_lines(user_request),
        *_operator_online_lookup_lines(user_request),
        *_operator_literal_payload_lines(user_request),
        *_operator_absolute_path_lines(),
        *_operator_contract_lines(
            _terminal_cwd_from_request(user_request),
            terminal_execution_enabled=_terminal_execution_enabled_from_request(user_request),
            verification_enforced=verification_enforced,
            shell_input_bindings_mode=_shell_input_bindings_mode_from_request(user_request),
        ),
        "Schema:",
        _stable_json(OperatorPlan.model_json_schema()),
    ]
    if isinstance(intent_block, dict):
        lines.extend(["Intent:", _stable_json(_operator_prompt_intent_block(intent_block))])
    if feedback:
        lines.extend(["Validation feedback:", _stable_json(feedback)])
    lines.extend(["Prompt:", user_request.raw_prompt])
    return "\n".join(lines)


def _machine_repair_prompt(
    user_request: UserRequest,
    feedback: list[dict[str, Any]],
    rejected_plan: OperatorPlan | None = None,
) -> str:
    lines = [
        *prompt_lines("operator.machine_repair"),
        *_operator_clarification_lines(user_request),
        *_operator_policy_note_lines(user_request),
        *_operator_streaming_step_lines(user_request),
        *_operator_user_macro_lines(user_request),
        *_operator_online_lookup_lines(user_request),
        *_operator_literal_payload_lines(user_request),
        *_operator_absolute_path_lines(),
        *_operator_contract_lines(
            _terminal_cwd_from_request(user_request),
            terminal_execution_enabled=_terminal_execution_enabled_from_request(user_request),
            verification_enforced=_operator_verification_enforced_from_request(user_request),
            shell_input_bindings_mode=_shell_input_bindings_mode_from_request(user_request),
        ),
        "Schema:",
        _stable_json(OperatorPlan.model_json_schema()),
        "Feedback:",
        _stable_json(feedback),
    ]
    if rejected_plan is not None:
        lines.extend(["Rejected plan:", rejected_plan.model_dump_json()])
    lines.extend(["Prompt:", user_request.raw_prompt])
    return "\n".join(lines)


def _machine_plan_audit_prompt(user_request: UserRequest, plan: OperatorPlan) -> str:
    return "\n".join(
        [
            *prompt_lines("operator.machine_plan_audit"),
            *_operator_clarification_lines(user_request),
            *_operator_policy_note_lines(user_request),
            "Schema:",
            _stable_json(MachinePlanAudit.model_json_schema()),
            "Prompt:",
            user_request.raw_prompt,
            "Plan:",
            plan.model_dump_json(),
        ]
    )


def _machine_memory_audit_prompt(
    user_request: UserRequest,
    plan: OperatorPlan,
    directives: list[dict[str, Any]],
    deterministic_errors: list[dict[str, Any]],
    *,
    source: str,
) -> str:
    return "\n".join(
        [
            *prompt_lines("operator.machine_memory_audit"),
            "Memory guard rules:",
            _stable_json(memory_guard_rule_summaries()),
            "Schema:",
            _stable_json(MachineMemoryComplianceAudit.model_json_schema()),
            "Source:",
            source,
            "Directives:",
            _stable_json(directives),
            "Deterministic errors:",
            _stable_json(deterministic_errors),
            "Prompt:",
            user_request.raw_prompt,
            "Plan:",
            plan.model_dump_json(),
        ]
    )


class MachineOperatorPipeline(LLMOperatorPipeline):
    """Compact non-verbose operator pipeline using shared execution machinery."""

    def _complete_self_brief(
        self,
        user_request: UserRequest,
        conversation_context: dict[str, Any] | None = None,
    ) -> OperatorSelfBrief:
        prompt = _machine_self_brief_prompt(user_request, conversation_context)
        compact = self._call_with_max_tokens(
            self.llm_client,
            prompt,
            MachineSelfBrief,
            max_tokens=min(
                768,
                int(
                    operator_legacy_override(
                        self.config,
                        "llm_operator_self_brief_max_tokens",
                        reasoning_settings_from_profile(
                            getattr(self.config, "reasoning_profile", None)
                        ).self_brief_max_tokens,
                    )
                ),
            ),
        )
        return _operator_self_brief_from_machine(compact)

    def _ensure_self_brief(
        self,
        user_request: UserRequest,
        observability=None,
        conversation_context: dict[str, Any] | None = None,
    ) -> OperatorSelfBrief:
        existing = _operator_self_brief_from_request(user_request)
        if existing is not None:
            if not bool(dict(user_request.session_context or {}).get("agent_memory_retrieved_after_self_brief")):
                self._attach_memory_after_self_brief(user_request, existing, observability)
            return existing
        brief = super()._ensure_self_brief(user_request, observability, conversation_context)
        user_request.session_context["llm_contract_mode"] = "compact"
        return brief

    def _complete_plan(
        self,
        user_request: UserRequest,
        feedback: list[dict[str, Any]] | None = None,
    ) -> OperatorPlan:
        return structured_call(self.llm_client, _machine_plan_prompt(user_request, feedback), OperatorPlan)

    def _complete_repair(
        self,
        user_request: UserRequest,
        feedback: list[dict[str, Any]],
        rejected_plan: OperatorPlan | None = None,
    ) -> OperatorPlan:
        return structured_call(
            self.llm_client,
            _machine_repair_prompt(user_request, feedback, rejected_plan),
            OperatorPlan,
        )

    def _review_validated_plan(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        observability=None,
    ) -> OperatorPlan:
        review_override = operator_legacy_override(
            self.config,
            "llm_operator_plan_review_enabled",
        )
        review_enabled = (
            bool(review_override)
            if review_override is not None
            else reasoning_settings_from_profile(
                getattr(self.config, "reasoning_profile", None)
            ).plan_review_enabled
        )
        if review_override is None and not self._operator_profile_policy().run_plan_review:
            return self._normalize_plan_interactions(plan, observability)
        if not review_enabled:
            return self._normalize_plan_interactions(plan, observability)
        current_plan = self._normalize_plan_interactions(plan, observability)
        try:
            audit = structured_call(
                self.llm_client,
                _machine_plan_audit_prompt(user_request, current_plan),
                MachinePlanAudit,
            )
        except Exception as exc:
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_PLAN_REVIEW_REJECTED,
                title="Machine plan audit ignored",
                summary="Compact plan audit failed schema validation; continuing with the validated plan.",
                details={"error": str(exc), "llm_contract_mode": "compact"},
            )
            return current_plan
        self._emit(
            observability,
            level="info",
            event_type=OPERATOR_PLAN_REVIEW_PROPOSED,
            title="Machine plan audited",
            summary="Compact machine audit reviewed the validated operator plan.",
            details={
                "decision": audit.decision,
                "issues": list(audit.issues),
                "repair_guidance": audit.repair_guidance,
                "confidence": audit.confidence,
                "llm_contract_mode": "compact",
            },
        )
        if audit.decision == "accept":
            self._emit(
                observability,
                level="info",
                event_type=OPERATOR_PLAN_REVIEW_ACCEPTED,
                title="Machine plan audit accepted",
                summary="Compact machine audit accepted the validated plan.",
                details={"confidence": audit.confidence, "llm_contract_mode": "compact"},
            )
            return current_plan
        if audit.decision == "block":
            raise OperatorValidationError(
                [
                    {
                        "error": "machine_plan_audit_blocked",
                        "message": audit.repair_guidance
                        or "; ".join(audit.issues)
                        or "Compact machine audit blocked the plan.",
                        "issues": list(audit.issues),
                        "confidence": audit.confidence,
                    }
                ]
            )
        feedback = [
            {
                "error": "machine_plan_audit_repair_required",
                "message": "Compact machine audit requested repair.",
                "issues": list(audit.issues),
                "repair_hint": audit.repair_guidance,
                "confidence": audit.confidence,
            }
        ]
        try:
            repaired = self._complete_repair(user_request, feedback, current_plan)
            repaired = self._normalize_plan_interactions(
                self._defer_dependent_python_actions(repaired, observability),
                observability,
            )
            errors = self._validate_plan(user_request, repaired, observability)
        except Exception as exc:
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_PLAN_REVIEW_REJECTED,
                title="Machine audit repair ignored",
                summary="Compact audit repair failed; continuing with the last validated plan.",
                details={"error": str(exc), "feedback": feedback, "llm_contract_mode": "compact"},
            )
            return current_plan
        if errors:
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_PLAN_REVIEW_REJECTED,
                title="Machine audit repair rejected",
                summary="Compact audit repair failed deterministic validation; continuing with the last validated plan.",
                details={"errors": errors, "feedback": feedback, "llm_contract_mode": "compact"},
            )
            return current_plan
        self._emit(
            observability,
            level="warning",
            event_type=OPERATOR_PLAN_REVIEW_ACCEPTED,
            title="Machine audit repaired plan",
            summary="Compact machine audit repaired the operator plan through the normal repair path.",
            details={"issues": list(audit.issues), "action_count": len(repaired.actions), "llm_contract_mode": "compact"},
        )
        return repaired

    def _review_memory_compliance(
        self,
        user_request: UserRequest,
        plan: OperatorPlan,
        observability=None,
        *,
        source: str = "fresh_plan",
    ) -> tuple[OperatorPlan, list[dict[str, Any]]]:
        directives = [
            directive
            for directive in _memory_directives_for_stage(user_request, "plan_review")
            if str(directive.get("strength") or "") in {"must_consider", "required_unless_conflict"}
        ]
        if not directives:
            return plan, []
        directives, ignored_directives, memory_intent_scope = _memory_directives_for_current_intent(
            user_request,
            plan,
            directives,
        )
        if not directives:
            self._emit(
                observability,
                level="info",
                event_type=OPERATOR_MEMORY_COMPLIANCE_REVIEWED,
                title="Machine memory compliance ignored by intent",
                summary="Retrieved memory directives were read-only/status scoped and did not apply to the current requested mutation.",
                details={
                    "source": source,
                    "decision": "ignore_memory",
                    "ignored_directives": ignored_directives,
                    "memory_intent_scope": memory_intent_scope,
                    "llm_contract_mode": "compact",
                },
            )
            return plan, []
        deterministic_errors = _memory_constraint_errors(user_request, plan, directives)
        try:
            audit = structured_call(
                self.llm_client,
                _machine_memory_audit_prompt(
                    user_request,
                    plan,
                    directives,
                    deterministic_errors,
                    source=source,
                ),
                MachineMemoryComplianceAudit,
            )
        except Exception as exc:
            self._emit(
                observability,
                level="warning",
                event_type=OPERATOR_MEMORY_COMPLIANCE_REJECTED,
                title="Machine memory audit failed",
                summary="Compact memory compliance audit failed schema validation.",
                details={
                    "source": source,
                    "error": str(exc),
                    "deterministic_errors": deterministic_errors,
                    "llm_contract_mode": "compact",
                },
            )
            audit = None
        if audit is not None:
            self._emit(
                observability,
                level="info",
                event_type=OPERATOR_MEMORY_COMPLIANCE_REVIEWED,
                title="Machine memory compliance audited",
                summary="Compact machine audit checked the plan against retrieved memory directives.",
                details={
                    "source": source,
                    "decision": audit.decision,
                    "violated_memory_ids": list(audit.violated_memory_ids),
                    "repair_guidance": audit.repair_guidance,
                    "confidence": audit.confidence,
                    "deterministic_errors": deterministic_errors,
                    "ignored_directives": ignored_directives,
                    "memory_intent_scope": memory_intent_scope,
                    "llm_contract_mode": "compact",
                },
            )
            if audit.decision in {"accept", "ignore_memory"} and not deterministic_errors:
                return plan, []
            if (
                audit.decision == "repair_required"
                and not deterministic_errors
                and audit.confidence < 0.55
            ):
                self._emit(
                    observability,
                    level="warning",
                    event_type=OPERATOR_MEMORY_COMPLIANCE_REJECTED,
                    title="Low-confidence machine memory repair ignored",
                    summary="Compact memory requested repair without deterministic errors or enough confidence; the validated plan was kept.",
                    details={
                        "source": source,
                        "decision": audit.decision,
                        "violated_memory_ids": list(audit.violated_memory_ids),
                        "repair_guidance": audit.repair_guidance,
                        "confidence": audit.confidence,
                        "ignored_directives": ignored_directives,
                        "memory_intent_scope": memory_intent_scope,
                        "llm_contract_mode": "compact",
                    },
                )
                return plan, []
        guidance = ""
        memory_ids: list[str] = []
        if audit is not None:
            guidance = audit.repair_guidance
            memory_ids = list(audit.violated_memory_ids)
        feedback = [
            *deterministic_errors,
            {
                "error": "machine_memory_compliance_repair_required",
                "message": "Compact memory compliance audit requested repair.",
                "memory_ids": memory_ids,
                "repair_hint": guidance,
                "confidence": audit.confidence if audit is not None else 0.0,
            },
        ]
        if not deterministic_errors and (audit is None or audit.decision != "repair_required"):
            return plan, []
        try:
            candidate = self._complete_repair(user_request, feedback, plan)
            candidate = self._normalize_plan_interactions(
                self._defer_dependent_python_actions(candidate, observability),
                observability,
            )
            preservation_errors = _memory_repair_preservation_errors(user_request, plan, candidate)
            if preservation_errors:
                self._emit(
                    observability,
                    level="warning",
                    event_type=OPERATOR_MEMORY_COMPLIANCE_REJECTED,
                    title="Machine memory repair ignored by intent",
                    summary="The compact memory repair erased the current user-requested action, so the validated plan was kept.",
                    details={
                        "source": source,
                        "errors": preservation_errors,
                        "memory_feedback": feedback,
                        "ignored_directives": ignored_directives,
                        "memory_intent_scope": memory_intent_scope,
                        "llm_contract_mode": "compact",
                    },
                )
                return plan, []
            validation_errors = self._validate_plan(user_request, candidate, observability)
            remaining_errors = _memory_constraint_errors(user_request, candidate, directives)
        except Exception as exc:
            return plan, [*feedback, {"error": "machine_memory_compliance_repair_failed", "message": str(exc)}]
        if validation_errors or remaining_errors:
            return plan, [
                *feedback,
                {
                    "error": "machine_memory_compliance_repair_rejected",
                    "message": "Compact memory repair still failed checks.",
                    "validation_errors": validation_errors,
                    "memory_errors": remaining_errors,
                },
            ]
        self._emit(
            observability,
            level="warning",
            event_type=OPERATOR_MEMORY_COMPLIANCE_REPAIRED,
            title="Machine memory compliance repaired plan",
            summary="The compact machine path repaired the plan to satisfy retrieved memory directives.",
            details={
                "source": source,
                "action_count": len(candidate.actions),
                "task_count": len(candidate.tasks),
                "memory_feedback": feedback,
                "llm_contract_mode": "compact",
            },
        )
        return candidate, []


__all__ = [
    "MachineOperatorPipeline",
    "MachineClassificationProposal",
    "MachineDecompositionProposal",
    "MachinePlanAudit",
    "MachineMemoryComplianceAudit",
    "classify_prompt_compact",
    "decompose_prompt_compact",
]
