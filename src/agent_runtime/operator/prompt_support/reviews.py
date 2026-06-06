"""Self-brief, deliberation, and review prompt builders."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.operator.prompt_support.common import *
from agent_runtime.operator.prompt_support.context import *

def build_operator_self_brief_prompt(
    user_request: UserRequest,
    conversation_context: dict[str, Any] | None = None,
) -> str:
    """Build a compact preflight prompt for domain facts, pitfalls, and postconditions."""

    mode_label = str(user_request.session_context.get("operator_mode_label") or "Conversational").strip()
    intent_block = user_request.session_context.get("operator_intent_block")
    current_streaming_task = dict(user_request.session_context or {}).get(
        "operator_streaming_current_task"
    )
    streaming_step_scope = isinstance(current_streaming_task, dict)
    scope_label = "the current streaming step" if streaming_step_scope else "the user's request"
    verification_enforced = _operator_verification_enforced_from_request(user_request)
    lines = [
        *prompt_lines(
            "operator.self_brief",
            {
                "mode_label": mode_label,
                "scope_label": scope_label,
                "streaming_scope_instruction": (
                    "Streaming scope: prepare this self-brief only for the current decomposed task. "
                    "The original user request is background context and must not expand this brief to later steps."
                    if streaming_step_scope
                    else "Prepare this self-brief for the current user request."
                ),
                "verification_strategy_instruction": (
                    "verification_strategy should describe how to prove completion without relying on pre-mutation data."
                    if verification_enforced
                    else "verification_strategy should state that no separate verification action is required unless the user explicitly asked for verification, the command result is ambiguous, or a check is needed to avoid a false success claim."
                ),
            },
        ),
        *_operator_verification_mode_lines(verification_enforced=verification_enforced),
        f"Self-question answers must be direct planning facts for {scope_label}. Do not suggest dry-runs, optional extra safety steps, or alternative workflows unless the user asked for them or they are needed for non-interactive correctness.",
        "If you do not know a domain fact, say so briefly instead of inventing it.",
        *_operator_discoverability_lines(),
        *_operator_absolute_path_lines(),
        *_operator_clarification_lines(user_request),
        *_operator_policy_note_lines(user_request),
        *_operator_user_macro_lines(user_request),
        *_operator_online_lookup_lines(user_request),
        *_operator_literal_payload_lines(user_request),
        *_operator_streaming_step_lines(user_request),
        *_operator_execution_shape_lines(user_request),
        "Return an OperatorSelfBrief object matching the target JSON schema supplied with this call.",
    ]
    if isinstance(intent_block, dict):
        lines.extend(
            [
                "Agent operator intent block:",
                _stable_json(_operator_prompt_intent_block(intent_block)),
            ]
        )
    if isinstance(conversation_context, dict):
        lines.extend(
            [
                "Conversation context summary:",
                _stable_json(conversation_context),
            ]
        )
    lines.extend(["User prompt:", user_request.raw_prompt])
    return "\n".join(lines)


def build_operator_plan_review_prompt(
    user_request: UserRequest,
    plan: OperatorPlan,
    previous_review: OperatorPlanReview | None = None,
) -> str:
    """Build a structured LLM quality-review prompt for one valid operator plan."""

    mode_label = str(user_request.session_context.get("operator_mode_label") or "Conversational").strip()
    verification_enforced = _operator_verification_enforced_from_request(user_request)
    lines = [
        *prompt_lines(
            "operator.plan_review",
            {
                "mode_label": mode_label,
                "verification_review_instruction": (
                    "Revise mutating plans when they lack a same-action or downstream verification step before claiming success."
                    if verification_enforced
                    else "Do not revise a plan merely to add a separate verification action when verification is relaxed; revise only for user-requested verification, ambiguous command success, stale data, missing requested output, or a concrete false-success risk."
                ),
            },
        ),
        "For read-only listing, counting, summarization, and computed-answer requests, do not require an extra verification action when the planned fresh command/Python action itself produces the requested rows/value and raises or exits nonzero on parse failure.",
        "Do not add redundant verifier actions solely to restate a computed answer; prefer one action that computes truthfully from fresh data, or deferred Python that raises when required rows or values cannot be parsed.",
        "Revise or remove verification actions that echo placeholders, reference files that no prior action created, or try to consume previous action output from shell without recomputing it or using a Python action with input_bindings.",
        "For read-only computed answers, prefer removing brittle extra verification steps when the producer action already emits the requested rows/value and fails on parse errors.",
        "Revise verification steps that violate verification_contract.freshness; for fresh_post_action_read, verification must read fresh post-mutation state.",
        "Revise verification actions whose output or exit semantics do not match verification_contract.success_when, verification_contract.failure_when, and verification_contract.must_exit_nonzero_when_unmet.",
        "When reviewing an executable verifier, evaluate both branches: what happens when success_when is true, and what happens when failure_when is true. If failure_when can be true while the verifier exits 0, revise.",
        *_operator_verification_mode_lines(verification_enforced=verification_enforced),
        "Keep the plan general. Do not add domain-specific hacks; improve the command/code strategy for the stated user goal.",
        "If revising, return the whole corrected OperatorPlan with consistent task_id/action_id/dependency/input_binding references.",
        "Plan review should improve action strategy, dependencies, and verification shape; do not eagerly author concrete Python code for an upstream-dependent action whose data shape is not available yet.",
        "Do not reject a deferred Python action merely because code is null. If it has input_bindings and defer_code_generation true, that is intentional; the runtime will generate concrete code after upstream output exists.",
        "For deferred Python, review the action goal, bindings, dependencies, risk, and downstream verification. Do not review code quality until the deferred code-generation stage.",
        "Never make required_revision say to implement or refine a deferred Python body during plan review. That is handled by the later deferred code-generation and code-review stages.",
        "If the only remaining concern is that a deferred Python body has not been generated yet, decision must be accept.",
        "If the plan has deferred Python plus a fresh downstream verifier with explicit if/then/else success and failure exits, accept it unless there is another concrete non-deferred flaw.",
        "If a python_action or python_transform has input_bindings, keep code null and defer_code_generation true unless you are replacing it with one self-contained Python action that no longer depends on upstream output.",
        "For simple fresh-state verification, prefer a concrete shell_command using the simplest stable state evidence; do not introduce Python merely to wrap one CLI verification check.",
        "If a revised plan includes concrete python_action code, the source must start exactly with def main(inputs): and all imports must be inside that function.",
        "If a revised plan includes concrete python_transform code, the source must start exactly with def transform(inputs): and all imports must be inside that function.",
        "For calculations or aggregation, prefer one python_action that owns subprocess/data handling, or defer downstream Python until upstream data shape is known.",
        "For read-only existence checks, commands should print a clear found/not-found result and exit 0 for both outcomes.",
        "For post-mutation verification, commands should print verified/failed and exit according to the verification_contract.",
        "For target_absent verification, accept any shell or Python verifier whose success branch proves absence and whose failure branch exits nonzero when the target remains.",
        "Do not accept a verifier that merely prints matching rows or a success message while exiting 0 when verification_contract.failure_when is true.",
        "For command output that will be parsed, prefer machine-readable flags such as --format, --json, --porcelain, or explicit columns when supported.",
        *_operator_discoverability_lines(),
        *memory_prompt_lines_from_context(user_request, stage="plan_review"),
        *parameter_prompt_lines_from_context(user_request, stage="plan_review"),
        *_operator_clarification_lines(user_request),
        *_operator_policy_note_lines(user_request),
        *_operator_self_brief_lines(user_request),
        *_operator_user_macro_lines(user_request),
        *_operator_online_lookup_lines(user_request),
        *_operator_literal_payload_lines(user_request),
        *_guided_deliberation_lines(user_request),
        *_operator_contract_lines(
            _terminal_cwd_from_request(user_request),
            terminal_execution_enabled=_terminal_execution_enabled_from_request(user_request),
            verification_enforced=verification_enforced,
            shell_input_bindings_mode=_shell_input_bindings_mode_from_request(user_request),
        ),
        "OperatorPlanReview schema:",
        _stable_json(OperatorPlanReview.model_json_schema()),
        "User prompt:",
        user_request.raw_prompt,
        "Validated plan under review:",
        plan.model_dump_json(indent=2),
    ]
    if previous_review is not None:
        lines.extend(
            [
                "Previous plan review feedback:",
                _stable_json(
                    {
                        "decision": previous_review.decision,
                        "strategy_alignment": previous_review.strategy_alignment,
                        "postcondition_alignment": previous_review.postcondition_alignment,
                        "verification_contract_alignment": previous_review.verification_contract_alignment,
                        "verification_gap": previous_review.verification_gap,
                        "required_revision": previous_review.required_revision,
                        "issues": list(previous_review.issues),
                        "reason": previous_review.reason,
                    }
                ),
                "This is a follow-up review of the revised plan. Focus on whether the current plan fixed the previous required_revision.",
                "If the previous required_revision is satisfied and remaining Python work is intentionally deferred, accept the plan.",
                "Do not invent new requirements during follow-up review unless the current plan has a concrete non-deferred flaw that would make execution unsafe or unable to satisfy the user.",
            ]
        )
    return "\n".join(lines)


def build_deliberation_frame_prompt(
    user_request: UserRequest,
    brief: OperatorSelfBrief,
    *,
    reason: str,
) -> str:
    """Build a concise guided reasoning frame prompt."""

    return "\n".join(
        [
            *prompt_lines("operator.deliberation_frame"),
            "DeliberationFrame schema:",
            _stable_json(DeliberationFrame.model_json_schema()),
            "Deep Reasoning trigger reason:",
            reason,
            "Operator self-brief:",
            brief.model_dump_json(indent=2),
            "Memory constraints:",
            _stable_json(_memory_directives_for_stage(user_request, "operator_plan")),
            "User prompt:",
            user_request.raw_prompt,
        ]
    )


def build_plan_critique_prompt(
    user_request: UserRequest,
    plan: OperatorPlan,
    *,
    phase: str,
    previous_critique: PlanCritique | None = None,
) -> str:
    """Build a guided critique prompt for a candidate plan."""

    lines = [
        *prompt_lines("operator.plan_critique"),
        *memory_prompt_lines_from_context(user_request, stage="plan_review"),
        *parameter_prompt_lines_from_context(user_request, stage="plan_review"),
        *_operator_clarification_lines(user_request),
        *_operator_policy_note_lines(user_request),
        *_operator_self_brief_lines(user_request),
        *_operator_user_macro_lines(user_request),
        *_operator_online_lookup_lines(user_request),
        *_operator_literal_payload_lines(user_request),
        *_guided_deliberation_lines(user_request),
        *_operator_contract_lines(
            _terminal_cwd_from_request(user_request),
            terminal_execution_enabled=_terminal_execution_enabled_from_request(user_request),
            verification_enforced=_operator_verification_enforced_from_request(user_request),
            shell_input_bindings_mode=_shell_input_bindings_mode_from_request(user_request),
        ),
        "PlanCritique schema:",
        _stable_json(PlanCritique.model_json_schema()),
        "Critique phase:",
        phase,
        "User prompt:",
        user_request.raw_prompt,
        "Candidate plan:",
        plan.model_dump_json(indent=2),
    ]
    if previous_critique is not None:
        lines.extend(
            [
                "Previous guided critique:",
                previous_critique.model_dump_json(indent=2),
                "This is a follow-up critique. Accept if the current plan fixed the previous issue and no concrete blocker remains.",
            ]
        )
    return "\n".join(lines)


def build_code_generation_critique_prompt(
    user_request: UserRequest,
    action: OperatorAction,
    proposal: OperatorPythonCodeProposal,
    *,
    input_packet: dict[str, Any],
    runtime_contract: dict[str, Any],
) -> str:
    """Build a guided critique prompt for generated Python code."""

    return "\n".join(
        [
            *prompt_lines("operator.code_generation_critique"),
            "For list/entity iteration, prefer code that catches per-item exceptions, records item-level errors, and continues processing remaining items unless the action truly requires all items to succeed.",
            *_operator_discoverability_lines(),
            *memory_prompt_lines_from_context(user_request, stage="code_generation"),
            *parameter_prompt_lines_from_context(user_request, stage="code_generation"),
            *_operator_clarification_lines(user_request),
            *_operator_policy_note_lines(user_request),
            *_operator_self_brief_lines(user_request),
            *_operator_streaming_step_lines(user_request),
            *_operator_execution_shape_lines(user_request),
            *_operator_user_macro_lines(user_request),
            *_operator_online_lookup_lines(user_request),
            *_operator_literal_payload_lines(user_request),
            *_guided_deliberation_lines(user_request),
            *_operator_verification_mode_lines(
                verification_enforced=_operator_verification_enforced_from_request(user_request)
            ),
            "CodeGenerationCritique schema:",
            _stable_json(CodeGenerationCritique.model_json_schema()),
            "User prompt:",
            user_request.raw_prompt,
            "Action:",
            action.model_dump_json(indent=2),
            "Runtime input contract:",
            _stable_json(runtime_contract),
            "Authoring input preview packet:",
            _stable_json(input_packet),
            "Generated proposal:",
            proposal.model_dump_json(indent=2),
        ]
    )


def build_evidence_satisfaction_review_prompt(
    user_request: UserRequest,
    plan: OperatorPlan,
    records: list[OperatorExecutionRecord],
) -> str:
    """Build a guided post-execution satisfaction review prompt."""

    return "\n".join(
        [
            *prompt_lines("operator.evidence_satisfaction_review"),
            *memory_prompt_lines_from_context(user_request, stage="evidence_review"),
            *parameter_prompt_lines_from_context(user_request, stage="evidence_review"),
            *_operator_clarification_lines(user_request),
            *_operator_policy_note_lines(user_request),
            *_operator_self_brief_lines(user_request),
            *_guided_deliberation_lines(user_request),
            "EvidenceSatisfactionReview schema:",
            _stable_json(EvidenceSatisfactionReview.model_json_schema()),
            "User prompt:",
            user_request.raw_prompt,
            "Executed plan:",
            plan.model_dump_json(indent=2),
            "Execution records:",
            _stable_json({"records": records}),
        ]
    )


def build_memory_compliance_review_prompt(
    user_request: UserRequest,
    plan: OperatorPlan,
    directives: list[dict[str, Any]],
    deterministic_errors: list[dict[str, Any]] | None = None,
    *,
    source: str = "fresh_plan",
) -> str:
    """Build a structured review prompt for memory compliance."""

    return "\n".join(
        [
            *prompt_lines("operator.memory_compliance_review"),
            "Deterministic memory guard rules:",
            _stable_json(memory_guard_rule_summaries()),
            "MemoryComplianceReview schema:",
            _stable_json(MemoryComplianceReview.model_json_schema()),
            "Review source:",
            source,
            "Memory directives:",
            _stable_json(directives),
            "Deterministic memory checks:",
            _stable_json(deterministic_errors or []),
            "User prompt:",
            user_request.raw_prompt,
            "Candidate plan:",
            plan.model_dump_json(indent=2),
        ]
    )


__all__ = [name for name in globals() if not name.startswith("__")]
