"""Plan, cache, command-template, and computation prompt builders."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.operator.prompt_support.common import *
from agent_runtime.operator.prompt_support.context import *

def build_operator_plan_prompt(user_request: UserRequest, feedback: list[dict[str, Any]] | None = None) -> str:
    """Build the LLM-facing operator planning prompt without raw runtime payloads."""

    mode_label = str(user_request.session_context.get("operator_mode_label") or "Conversational").strip()
    intent_block = user_request.session_context.get("operator_intent_block")
    is_streaming_step = (
        isinstance(intent_block, dict)
        and str(intent_block.get("mode") or "") == "decomposition_streaming_step"
    )
    verification_enforced = _operator_verification_enforced_from_request(user_request)
    lines = [
        *prompt_lines("operator.plan", {"mode_label": mode_label}),
        *_operator_discoverability_lines(),
        *memory_prompt_lines_from_context(user_request, stage="operator_plan"),
        *parameter_prompt_lines_from_context(user_request, stage="operator_plan"),
        *_operator_clarification_lines(user_request),
        *_operator_policy_note_lines(user_request),
        *_operator_self_brief_lines(user_request),
        *_operator_streaming_step_lines(user_request),
        *_operator_execution_shape_lines(user_request),
        *_operator_gateway_context_lines(user_request),
        *_operator_user_macro_lines(user_request),
        *_operator_online_lookup_lines(user_request),
        *_operator_literal_payload_lines(user_request),
        *_operator_fast_trout_evidence_lines(user_request),
        *_guided_deliberation_lines(user_request),
        *_operator_contract_lines(
            _terminal_cwd_from_request(user_request),
            terminal_execution_enabled=_terminal_execution_enabled_from_request(user_request),
            verification_enforced=verification_enforced,
            shell_input_bindings_mode=_shell_input_bindings_mode_from_request(user_request),
        ),
        "Schema for OperatorPlan:",
        _operator_schema_prompt(),
    ]
    if isinstance(intent_block, dict):
        lines.extend(
            [
                "Agent operator intent block:",
                _stable_json(_operator_prompt_intent_block(intent_block)),
                (
                    "Use this block as semantic intent only. Author one coherent operator plan for the current decomposed streaming step only."
                    if is_streaming_step
                    else "Use this block as semantic intent only. Author one coherent operator plan for the whole user request."
                ),
                (
                    "Do not include later decomposed tasks in this plan; the runtime will call you again for the next step with this step's output."
                    if is_streaming_step
                    else "If tasks are present, do not turn separate decomposed tasks into unrelated commands; keep paths, counts, sizes, and transforms consistent across actions."
                ),
            ]
        )
    if feedback:
        lines.extend(
            [
                "Previous operator plan validation feedback:",
                _stable_json(feedback),
                "Repair the plan once. Keep legal parts, fix only invalid fields, and return an OperatorPlan JSON object.",
            ]
        )
    lines.extend(["User prompt:", user_request.raw_prompt])
    return "\n".join(lines)


def build_single_report_compiler_prompt(
    user_request: UserRequest,
    validation_errors: list[dict[str, Any]],
    rejected_plan: OperatorPlan | None = None,
    conversation_context: dict[str, Any] | None = None,
) -> str:
    """Build the retired execution-shape compiler prompt."""

    lines = [
        *prompt_lines("operator.plan", {"mode_label": "Retired Shape Compiler"}),
        "Execution-shape compiler mode is retired.",
        "Return an ordinary OperatorPlan for the current request scope only.",
        "Do not collapse decomposed work into one task or one action unless the request scope is already atomic.",
        "Prefer granular tasks and normal operator validation; decomposition and streaming remain in control.",
        "Validation feedback that reached this compatibility prompt:",
        _stable_json(validation_errors),
        "Treat validation feedback as normal repair guidance, not as a request for a collapsed report action.",
        *_operator_discoverability_lines(),
        *parameter_prompt_lines_from_context(user_request, stage="operator_plan"),
        *_operator_clarification_lines(user_request),
        *_operator_policy_note_lines(user_request),
        *_operator_execution_shape_lines(user_request),
        *_operator_gateway_context_lines(user_request),
        *_operator_user_macro_lines(user_request),
        *_operator_online_lookup_lines(user_request),
        *_operator_literal_payload_lines(user_request),
        *_operator_contract_lines(
            _terminal_cwd_from_request(user_request),
            terminal_execution_enabled=_terminal_execution_enabled_from_request(user_request),
            verification_enforced=_operator_verification_enforced_from_request(user_request),
            shell_input_bindings_mode=_shell_input_bindings_mode_from_request(user_request),
        ),
        "Schema for OperatorPlan:",
        _operator_schema_prompt(),
    ]
    if rejected_plan is not None:
        lines.extend(
            [
                "Rejected plan summary:",
                _single_report_rejected_plan_summary(rejected_plan),
            ]
        )
    if conversation_context is not None:
        lines.extend(
            [
                "Conversation context JSON:",
                _stable_json(conversation_context),
            ]
        )
    lines.extend(["User prompt:", user_request.raw_prompt])
    return "\n".join(lines)


def build_operator_rephrase_retry_prompt(
    user_request: UserRequest,
    validation_errors: list[dict[str, Any]],
) -> str:
    """Build the one-shot prompt rewrite contract used after validation exhaustion."""

    intent_block = user_request.session_context.get("operator_intent_block")
    is_streaming_step = (
        isinstance(intent_block, dict)
        and str(intent_block.get("mode") or "") == "decomposition_streaming_step"
    )
    scope = "the current decomposed streaming step" if is_streaming_step else "the current user request"
    lines = [
        *prompt_lines(
            "operator.rephrase_retry",
            {
                "scope": scope,
                "streaming_failure_rule": (
                    "If the failure was streaming_future_step_action, rephrase only the current "
                    "step and explicitly exclude later reserved operations. For "
                    "filter/select/sort/transform steps, say to produce only the narrowed "
                    "target list/result."
                ),
            },
        ),
        *_operator_clarification_lines(user_request),
        *_operator_streaming_step_lines(user_request),
        *_operator_execution_shape_lines(user_request),
        *_operator_user_macro_lines(user_request),
        *_operator_online_lookup_lines(user_request),
        *_operator_literal_payload_lines(user_request),
        "Validation errors that caused this retry:",
        _stable_json(validation_errors),
    ]
    if isinstance(intent_block, dict):
        lines.extend(
            [
                "Agent operator intent block:",
                _stable_json(_operator_prompt_intent_block(intent_block)),
            ]
        )
    lines.extend(
        [
            "OperatorRephraseRetryProposal schema:",
            _stable_json(OperatorRephraseRetryProposal.model_json_schema()),
            "Prompt to rewrite:",
            user_request.raw_prompt,
        ]
    )
    return "\n".join(lines)


def build_operator_plan_cache_prompt(
    user_request: UserRequest,
    candidate: PlanCacheCandidate,
) -> str:
    """Build the short LLM prompt that adapts one cached plan skeleton."""

    mode_label = str(user_request.session_context.get("operator_mode_label") or "Conversational").strip()
    entry = candidate.entry
    verification_enforced = _operator_verification_enforced_from_request(user_request)
    cached_payload = {
        "cache_id": entry.cache_id,
        "score": candidate.score,
        "reason": candidate.reason,
        "historical_prompt_excerpt": entry.prompt_excerpt,
        "mode": entry.mode,
        "task_type": entry.task_type,
        "tool_type": entry.tool_type,
        "intent_type": entry.intent_type,
        "tags": entry.tags,
        "self_brief": entry.self_brief,
        "plan": entry.plan,
        "action_summaries": entry.action_summaries,
        "risk_summary": entry.risk_summary,
        "historical_records_summary": entry.records_summary,
        "historical_final_response_preview": entry.final_response_preview,
        "repair_notes": entry.repair_notes,
    }
    lines = [
        *prompt_lines("operator.plan_cache", {"mode_label": mode_label}),
        *_operator_discoverability_lines(),
        *memory_prompt_lines_from_context(user_request, stage="cache"),
        *parameter_prompt_lines_from_context(user_request, stage="cache"),
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
        "Schema for OperatorPlanCacheDecision:",
        _stable_json(OperatorPlanCacheDecision.model_json_schema()),
        "Cached plan candidate:",
        _stable_json(cached_payload),
        "Current user prompt:",
        user_request.raw_prompt,
    ]
    return "\n".join(lines)


def build_operator_command_template_draft_prompt(
    user_request: UserRequest,
    plan: OperatorPlan,
    action: OperatorAction,
    record: OperatorExecutionRecord,
) -> str:
    """Build the short LLM prompt that learns one shell command template."""

    task = next((item for item in plan.tasks if item.task_id == action.task_id), None)
    lines = [
        *prompt_lines("operator.command_template_draft"),
        "Schema for OperatorCommandTemplateDraft:",
        _stable_json(OperatorCommandTemplateDraft.model_json_schema()),
        "Current user prompt:",
        user_request.raw_prompt,
        "Current decomposed/semantic task:",
        task.model_dump_json(indent=2) if task is not None else "{}",
        "Successful shell action:",
        action.model_dump_json(indent=2),
        "Successful execution record:",
        _stable_json(
            {
                "action_id": record.action_id,
                "task_id": record.task_id,
                "status": record.status,
                "exit_code": record.exit_code,
                "stdout_preview": _truncate(record.stdout, 800),
                "stderr_preview": _truncate(record.stderr, 500),
                "metadata": {
                    key: value
                    for key, value in dict(record.metadata or {}).items()
                    if key
                    in {
                        "cwd",
                        "shell_command",
                        "declared_output_shape",
                        "streaming_step_id",
                        "streaming_task_id",
                        "streaming_step_index",
                    }
                },
            }
        ),
    ]
    return "\n".join(lines)


def build_operator_llm_text_prompt(
    user_request: UserRequest,
    action: OperatorAction,
    inputs: dict[str, Any],
) -> str:
    """Build the compact prompt for one generated-text operator action."""

    def input_packet(value: Any) -> dict[str, Any]:
        text = value if isinstance(value, str) else _stable_json(value)
        text = str(text)
        return {
            "type": type(value).__name__,
            "value": _truncate(text, 20000),
            "value_chars": len(text),
            "truncated": len(text) > 20000,
        }

    return "\n".join(
        [
            *prompt_lines("operator.llm_text"),
            "OperatorTextGenerationDraft schema:",
            _stable_json(OperatorTextGenerationDraft.model_json_schema()),
            "User prompt:",
            user_request.raw_prompt,
            "Action llm_prompt:",
            str(action.llm_prompt or ""),
            "Action reason:",
            str(action.reason or ""),
            "Resolved runtime inputs:",
            _stable_json({str(key): input_packet(value) for key, value in sorted(dict(inputs or {}).items())}),
        ]
    )


def build_operator_command_template_cache_prompt(
    user_request: UserRequest,
    candidates: list[CommandTemplateCandidate],
) -> str:
    """Build the short LLM prompt that judges learned command-template reuse."""

    compact_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        entry = candidate.entry
        compact_candidates.append(
            {
                "template_id": entry.template_id,
                "score": candidate.score,
                "reason": candidate.reason,
                "historical_prompt_excerpt": entry.prompt_excerpt,
                "historical_step_excerpt": entry.step_excerpt,
                "mode": entry.mode,
                "gateway_platform": entry.gateway_platform,
                "task_type": entry.task_type,
                "tool_type": entry.tool_type,
                "intent_type": entry.intent_type,
                "interaction_mode": entry.interaction_mode,
                "tags": entry.tags,
                "command_template": entry.command_template,
                "variables": entry.variables,
                "risk": entry.risk,
                "effect_intent": entry.effect_intent,
                "effect_summary": entry.effect_summary,
                "requires_confirmation": entry.requires_confirmation,
                "approval_observed": entry.approval_observed,
                "success_count": entry.success_count,
                "use_count": entry.use_count,
            }
        )
    lines = [
        *prompt_lines("operator.command_template_cache"),
        *_operator_discoverability_lines(),
        *_operator_streaming_step_lines(user_request),
        *_operator_gateway_context_lines(user_request),
        *_operator_clarification_lines(user_request),
        *_operator_policy_note_lines(user_request),
        *_operator_literal_payload_lines(user_request),
        "Schema for OperatorCommandTemplateDecision:",
        _stable_json(OperatorCommandTemplateDecision.model_json_schema()),
        "Current user prompt:",
        user_request.raw_prompt,
        "Learned command template candidates:",
        _stable_json(compact_candidates),
    ]
    return "\n".join(lines)


def build_operator_computation_cache_prompt(
    user_request: UserRequest,
    action: OperatorAction,
    candidate: ComputationCacheCandidate,
    *,
    runtime_contract: dict[str, Any],
    input_packet: dict[str, Any],
) -> str:
    """Build the short LLM prompt that adapts one cached computation template."""

    entry = candidate.entry
    function_name = "main" if action.kind == "python_action" else "transform"
    cached_payload = {
        "cache_id": entry.cache_id,
        "score": candidate.score,
        "reason": candidate.reason,
        "historical_prompt_excerpt": entry.prompt_excerpt,
        "mode": entry.mode,
        "task_type": entry.task_type,
        "tool_type": entry.tool_type,
        "intent_type": entry.intent_type,
        "tags": entry.tags,
        "action_kind": entry.action_kind,
        "action_reason": entry.action_reason,
        "historical_input_profile": entry.input_profile,
        "code_template": entry.code_template,
        "declared_output_shape": entry.declared_output_shape,
        "allow_zero_result": entry.allow_zero_result,
        "template_reason": entry.template_reason,
        "historical_output_preview": entry.output_preview,
    }
    lines = [
        *prompt_lines("operator.computation_cache", {"function_name": function_name}),
        "Schema for OperatorComputationCacheDecision:",
        _stable_json(OperatorComputationCacheDecision.model_json_schema()),
        "Current user prompt:",
        user_request.raw_prompt,
        "Current action needing code:",
        action.model_dump_json(indent=2),
        "Current runtime input contract:",
        _stable_json(runtime_contract),
        "Current authoring input preview packet:",
        _stable_json(input_packet),
        "Cached computation template candidate:",
        _stable_json(cached_payload),
    ]
    return "\n".join(lines)


__all__ = [name for name in globals() if not name.startswith("__")]
