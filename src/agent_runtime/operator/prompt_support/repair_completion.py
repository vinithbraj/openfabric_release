"""Repair, failure-continuation, completion, validation, and follow-up prompt builders."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.operator.prompt_support.common import *
from agent_runtime.operator.prompt_support.context import *

def build_operator_repair_prompt(
    user_request: UserRequest,
    plan: OperatorPlan,
    execution_records: list[OperatorExecutionRecord],
    repair_feedback: list[dict[str, Any]] | None = None,
) -> str:
    """Build one structured execution-repair prompt from live execution records."""

    mode_label = str(user_request.session_context.get("operator_mode_label") or "Conversational").strip()
    try:
        max_record_chars = int(user_request.session_context.get("operator_repair_record_max_chars") or 50000)
    except (TypeError, ValueError):
        max_record_chars = 50000
    max_record_chars = max(1200, min(max_record_chars, 200000))
    action_by_id = {action.action_id: action for action in plan.actions}
    failure_records = [record for record in execution_records if record.status == "error"]
    live_records: list[dict[str, Any]] = []
    diagnostic_shell_failures: list[dict[str, Any]] = []
    for record in execution_records:
        action = action_by_id.get(record.action_id)
        stdout = str(record.stdout or "")
        stderr = str(record.stderr or "")
        output_text = _stable_json(record.output) if record.output is not None else ""
        metadata = dict(record.metadata or {})
        stdout_classifier = metadata.get("stdout_error_classifier")
        stdout_diagnostic_error = bool(metadata.get("stdout_diagnostic_error")) or (
            isinstance(stdout_classifier, dict)
            and stdout_classifier.get("stdout_usable") is False
            and str(stdout_classifier.get("reason") or "") != "command_not_found"
        )
        if (
            record.status == "error"
            and action is not None
            and action.kind == "shell_command"
            and stdout_diagnostic_error
        ):
            diagnostic_shell_failures.append(
                {
                    "action_id": record.action_id,
                    "task_id": record.task_id,
                    "failed_command": action.command,
                    "exit_code": record.exit_code,
                    "diagnostic": _truncate(
                        stdout.strip() or stderr.strip() or str(record.error or "").strip(),
                        4000,
                    ),
                    "stdout_error_classifier": stdout_classifier,
                    "repair_rule": (
                        "Do not repeat this exact shell command. Change the command "
                        "syntax/options or use a different safe implementation."
                    ),
                }
            )
        generated_code = str(record.metadata.get("generated_code") or "").strip()
        generated_from_deferred = bool(record.metadata.get("deferred_code_generated"))
        action_code = (
            str(action.code or "")
            if action is not None and action.kind in {"python_action", "python_transform"}
            and not generated_from_deferred
            else ""
        )
        live_records.append(
            {
                "action_id": record.action_id,
                "task_id": record.task_id,
                "kind": record.kind,
                "label": action.reason if action is not None else record.action_id,
                "command": action.command if action is not None and action.kind == "shell_command" else None,
                "llm_prompt": action.llm_prompt if action is not None and action.kind == "llm_text" else None,
                "code": action_code or generated_code or None,
                "generated_code": generated_code or None,
                "code_source": (
                    "generated_runtime_code"
                    if generated_code and generated_from_deferred
                    else "plan"
                    if action_code
                    else None
                ),
                "input_bindings": [
                    binding.model_dump(mode="json")
                    for binding in list(action.input_bindings if action is not None else [])
                ],
                "status": record.status,
                "exit_code": record.exit_code,
                "stdout": _truncate(stdout, max_record_chars),
                "stdout_chars": len(stdout),
                "stdout_truncated": len(stdout) > max_record_chars,
                "stderr": _truncate(stderr, max_record_chars),
                "stderr_chars": len(stderr),
                "stderr_truncated": len(stderr) > max_record_chars,
                "output": _truncate(output_text, max_record_chars),
                "output_chars": len(output_text),
                "output_truncated": len(output_text) > max_record_chars,
                "bound_inputs_preview": record.metadata.get("bound_inputs_preview"),
                "error": record.error,
                "metadata": record.metadata,
            }
        )
    failure_packet = {
        "mode": mode_label,
        "record_count": len(execution_records),
        "failed_action_ids": [record.action_id for record in failure_records],
        "expected_postcondition": {
            "user_goal": user_request.raw_prompt,
            "plan_expected_outputs": list(plan.expected_outputs),
            "plan_assumptions": list(plan.assumptions),
        },
        "diagnostic_shell_failures": diagnostic_shell_failures,
        "records": live_records,
    }
    return "\n".join(
        [
            *prompt_lines("operator.repair", {"mode_label": mode_label}),
            *memory_prompt_lines_from_context(user_request, stage="repair"),
            *parameter_prompt_lines_from_context(user_request, stage="repair"),
            *_operator_clarification_lines(user_request),
            *_operator_policy_note_lines(user_request),
            *_operator_self_brief_lines(user_request),
            *_operator_execution_shape_lines(user_request),
            *_operator_gateway_context_lines(user_request),
            *_operator_user_macro_lines(user_request),
            *_operator_online_lookup_lines(user_request),
            *_operator_literal_payload_lines(user_request),
            *_guided_deliberation_lines(user_request),
            *_operator_user_hint_lines(),
            "Read stderr and error fields carefully; command diagnostics are authoritative clues for the next corrected action.",
            "If diagnostic_shell_failures is non-empty, the failed shell command invocation was classified as diagnostic output rather than useful result output.",
            "For diagnostic shell failures, do not repeat the exact failed_command; repaired plans that replay it are invalid. Change syntax/options or choose a different safe implementation.",
            "If the failed output is usage/help text, unknown option, invalid option, invalid argument, or otherwise indicates wrong CLI syntax, use the shown usage as authoritative, or add a read-only help discovery action such as `<command> --help`, `<command> -h`, `<command> help`, or an equivalent manual/usage probe before retrying the real command.",
            "If the next safe action depends on user intent, preference, credentials, target choice, or an irreversible choice not present in the prompt or prior clarifications, set decision ask_user and provide one clarification_request.",
            "Do not ask for runtime-discoverable facts; inspect or recompute those in the repaired plan instead.",
            "When decision is ask_user, do not invent a corrected_plan. The runtime will resume this repair point after the user answers.",
            "When decision is repair_plan, corrected_plan is required and must use the live evidence plus any prior clarification answers.",
            "Do not request hidden state. If an upstream command already produced the needed data, bind it into Python and parse that exact input.",
            "If the failed step should generate natural-language text from runtime evidence, use llm_text for that generated text and bind its stdout/output downstream.",
            "If a Python action or transform failed, repair the Python code against the actual traceback, generated code, bound inputs, and upstream output previews shown in the packet.",
            "For Python subprocess failures, use the exact traceback, return code, command argv, stdout, and stderr in the packet to correct the subprocess invocation; do not repeat the same failing command.",
            "When typed report validation shows nonconforming observed values, change the data producer so it explicitly emits the requested typed field; do not keep parsing descriptions, names, messages, titles, or neighboring prose.",
            "If a Python subprocess failed because it needed an interactive terminal, repair by moving that operation into a shell_command with interaction_mode may_prompt instead of asking for discoverable facts or retrying captured Python.",
            "If a shell command failed with terminal_context_required or background_terminal_unavailable, do not repair it by adding sudo -n, password stdin, or captured non_interactive execution. Preserve shell_command interaction_mode may_prompt for genuine sudo/SSH/password prompts so the runtime can use the trusted terminal/typein path; if the command is only a read-only probe that does not need root, remove unnecessary sudo instead.",
            "For read-only mount diagnostics, prefer non-sudo commands such as findmnt, cat /proc/mounts, lsblk, blkid, or mount without sudo before using sudo for an operation that actually requires root.",
            "When stderr explains command usage, treat that diagnostic as authoritative and choose a corrected command or a fresh verification step.",
            "If a shell command failed, repair the concrete command using the actual stderr/stdout shown in the packet.",
            "Repair should be action-local: preserve successful actions and their action_id/task_id values; change only failed actions and downstream consumers that must change because of the failure.",
            "Never repeat successful mutating actions under new action_ids. If only a later failed action needs correction, keep completed actions unchanged and correct only the unfinished action or its required downstream checks.",
            "If previous repair attempts were rejected, address those rejection errors directly and return a different valid repair.",
            *_operator_contract_lines(
                _terminal_cwd_from_request(user_request),
                terminal_execution_enabled=_terminal_execution_enabled_from_request(user_request),
                defer_upstream_python=False,
                verification_enforced=_operator_verification_enforced_from_request(user_request),
                shell_input_bindings_mode=_shell_input_bindings_mode_from_request(user_request),
            ),
            "OperatorRepair schema:",
            _stable_json(OperatorRepair.model_json_schema()),
            "User prompt:",
            user_request.raw_prompt,
            "Original plan:",
            plan.model_dump_json(indent=2),
            "Live execution failure packet:",
            _stable_json(failure_packet),
            "Previous rejected repair attempts:",
            _stable_json(repair_feedback or []),
        ]
    )


def build_operator_repair_adjudication_prompt(
    user_request: UserRequest,
    plan: OperatorPlan,
    execution_records: list[OperatorExecutionRecord],
    repair: OperatorRepair,
    repair_feedback: list[dict[str, Any]] | None = None,
) -> str:
    """Build a compact semantic review prompt for one execution repair."""

    mode_label = str(user_request.session_context.get("operator_mode_label") or "Conversational").strip()
    action_by_id = {action.action_id: action for action in plan.actions}
    record_packet: list[dict[str, Any]] = []
    for record in execution_records:
        action = action_by_id.get(record.action_id)
        record_packet.append(
            {
                "action_id": record.action_id,
                "task_id": record.task_id,
                "kind": record.kind,
                "status": record.status,
                "exit_code": record.exit_code,
                "failed_command": (
                    action.command if action is not None and action.kind == "shell_command" else None
                ),
                "failed_code": (
                    _truncate(action.code, 6000)
                    if action is not None and action.kind in {"python_action", "python_transform"}
                    else None
                ),
                "stdout": _truncate(record.stdout, 12000),
                "stderr": _truncate(record.stderr, 12000),
                "error": record.error,
                "metadata": record.metadata,
            }
        )
    proposed_payload = repair.model_dump(mode="json")
    return "\n".join(
        [
            *prompt_lines("operator.repair_adjudication", {"mode_label": mode_label}),
            "OperatorRepairAdjudication schema:",
            _stable_json(OperatorRepairAdjudication.model_json_schema()),
            "User prompt:",
            user_request.raw_prompt,
            "Original plan summary:",
            plan.summary,
            "Original plan actions:",
            _stable_json([action.model_dump(mode="json") for action in plan.actions]),
            "Live execution failure packet:",
            _stable_json(
                {
                    "failed_action_ids": [
                        record.action_id for record in execution_records if record.status == "error"
                    ],
                    "records": record_packet,
                }
            ),
            "Proposed execution repair:",
            _stable_json(proposed_payload),
            "Previous rejected repair attempts:",
            _stable_json(repair_feedback or []),
        ]
    )


def build_operator_failure_continuation_prompt(
    user_request: UserRequest,
    plan: OperatorPlan,
    execution_records: list[OperatorExecutionRecord],
    *,
    continuation_notes: str = "",
) -> str:
    """Build the LLM prompt for continuing a partially failed operator run."""

    mode_label = str(user_request.session_context.get("operator_mode_label") or "Conversational").strip()
    action_by_id = {action.action_id: action for action in plan.actions}
    records_payload: list[dict[str, Any]] = []
    for record in execution_records:
        action = action_by_id.get(record.action_id)
        records_payload.append(
            {
                "action_id": record.action_id,
                "task_id": record.task_id,
                "kind": record.kind,
                "status": record.status,
                "exit_code": record.exit_code,
                "command": action.command if action is not None and action.kind == "shell_command" else None,
                "code": _truncate(action.code, 4000) if action is not None and action.kind != "shell_command" else None,
                "stdout": _truncate(record.stdout, 8000),
                "stderr": _truncate(record.stderr, 8000),
                "error": record.error,
                "metadata": record.metadata,
            }
        )
    failed_records = [record for record in execution_records if record.status == "error"]
    success_ids = [record.action_id for record in execution_records if record.status == "success"]
    return "\n".join(
        [
            *prompt_lines("operator.failure_continuation", {"mode_label": mode_label}),
            *_operator_gateway_context_lines(user_request),
            *_operator_contract_lines(
                _terminal_cwd_from_request(user_request),
                terminal_execution_enabled=_terminal_execution_enabled_from_request(user_request),
                defer_upstream_python=False,
                verification_enforced=_operator_verification_enforced_from_request(user_request),
                shell_input_bindings_mode=_shell_input_bindings_mode_from_request(user_request),
            ),
            "OperatorFailureContinuationReview schema:",
            _stable_json(OperatorFailureContinuationReview.model_json_schema()),
            "User prompt:",
            user_request.raw_prompt,
            "Optional user continuation notes:",
            continuation_notes or "none",
            "Original plan:",
            plan.model_dump_json(indent=2),
            "Successful action ids available for seed reuse:",
            _stable_json(success_ids),
            "Failed action ids:",
            _stable_json([record.action_id for record in failed_records]),
            "Execution records:",
            _stable_json(records_payload),
        ]
    )


def build_operator_completion_review_prompt(
    user_request: UserRequest,
    plan: OperatorPlan,
    execution_records: list[OperatorExecutionRecord],
) -> str:
    """Build a prompt that checks whether a successful mutating plan reached the requested end state."""

    mode_label = str(user_request.session_context.get("operator_mode_label") or "Conversational").strip()
    verification_enforced = _operator_verification_enforced_from_request(user_request)
    action_by_id = {action.action_id: action for action in plan.actions}
    records: list[dict[str, Any]] = []
    for record in execution_records:
        action = action_by_id.get(record.action_id)
        stdout = str(record.stdout or "")
        stderr = str(record.stderr or "")
        output_text = _stable_json(record.output) if record.output is not None else ""
        records.append(
            {
                "action_id": record.action_id,
                "task_id": record.task_id,
                "kind": record.kind,
                "label": action.reason if action is not None else record.action_id,
                "command": action.command if action is not None and action.kind == "shell_command" else None,
                "llm_prompt": action.llm_prompt if action is not None and action.kind == "llm_text" else None,
                "code": (
                    str(action.code or "")
                    if action is not None and action.kind in {"python_action", "python_transform"}
                    else record.metadata.get("generated_code")
                ),
                "status": record.status,
                "exit_code": record.exit_code,
                "stdout": _truncate(stdout, 50000),
                "stdout_chars": len(stdout),
                "stderr": _truncate(stderr, 50000),
                "stderr_chars": len(stderr),
                "output": _truncate(output_text, 50000),
                "output_chars": len(output_text),
                "error": record.error,
                "metadata": record.metadata,
            }
        )
    return "\n".join(
        [
            *prompt_lines("operator.completion_review", {"mode_label": mode_label}),
            *_operator_discoverability_lines(),
            *memory_prompt_lines_from_context(user_request, stage="plan_review"),
            *parameter_prompt_lines_from_context(user_request, stage="plan_review"),
            *_operator_clarification_lines(user_request),
            *_operator_policy_note_lines(user_request),
            *_operator_self_brief_lines(user_request),
            *_guided_deliberation_lines(user_request),
            *_operator_gateway_context_lines(user_request),
            *_operator_user_hint_lines(),
            "If a safe continuation depends on user intent rather than runtime output, return decision ask_user with one clarification_request.",
            (
                "If the user requested mutation over all matching items, decide complete only when the latest available outputs prove all matching items were handled."
                if verification_enforced
                else "If the user requested mutation over all matching items, prefer completion when executed commands succeeded and no live evidence contradicts the result; request more actions only when output is ambiguous or the user explicitly asked for verification."
            ),
            (
                "If multiple targets may exist, require evidence that no matching targets remain or propose a continuation plan that performs fresh verification and any remaining work."
                if verification_enforced
                else "If multiple targets may exist, do not add a fresh verification action solely for assurance when verification is relaxed; add one only if prior output cannot support a truthful final response."
            ),
            (
                "If a mutating action only changed a parsed/listed preview but did not prove the external state changed, return needs_more_actions."
                if verification_enforced
                else "If a mutating action reports success and command status is successful, do not require an extra proof step unless output suggests the external state may not have changed."
            ),
            (
                "If evidence is stale or missing, return needs_more_actions with a self-contained read-only verification plan, or a mutating continuation plan if the remaining target is already proven."
                if verification_enforced
                else "If evidence is stale or missing, finalize cautiously from successful execution records unless the user requested verification or missing output prevents a truthful answer."
            ),
            "Continuation plans must be self-contained, use fresh unique action_id values, and must not depend on action ids from the previous plan.",
            "Continuation plans go through normal validation and approval before any mutating action executes.",
            *_operator_contract_lines(
                _terminal_cwd_from_request(user_request),
                terminal_execution_enabled=_terminal_execution_enabled_from_request(user_request),
                defer_upstream_python=True,
                verification_enforced=_operator_verification_enforced_from_request(user_request),
                shell_input_bindings_mode=_shell_input_bindings_mode_from_request(user_request),
            ),
            "OperatorCompletionReview schema:",
            _stable_json(OperatorCompletionReview.model_json_schema()),
            "User prompt:",
            user_request.raw_prompt,
            "Executed plan:",
            plan.model_dump_json(indent=2),
            "Execution records:",
            _stable_json({"records": records}),
        ]
    )


def build_operator_step_validation_prompt(
    user_request: UserRequest,
    plan: OperatorPlan,
    execution_records: list[OperatorExecutionRecord],
    contract: OperatorStepValidationContract,
    artifact_evidence: dict[str, Any],
) -> str:
    """Build the typed LLM judge prompt for one decomposed/streaming step."""

    mode_label = str(user_request.session_context.get("operator_mode_label") or "Conversational").strip()
    action_by_id = {action.action_id: action for action in plan.actions}
    records: list[dict[str, Any]] = []
    for record in execution_records:
        action = action_by_id.get(record.action_id)
        stdout = str(record.stdout or "")
        stderr = str(record.stderr or "")
        output_text = _stable_json(record.output) if record.output is not None else ""
        metadata = dict(record.metadata or {})
        records.append(
            {
                "action_id": record.action_id,
                "task_id": record.task_id,
                "kind": record.kind,
                "label": action.reason if action is not None else record.action_id,
                "command": action.command if action is not None and action.kind == "shell_command" else None,
                "code": (
                    str(action.code or "")
                    if action is not None and action.kind in {"python_action", "python_transform"}
                    else metadata.get("generated_code")
                ),
                "status": record.status,
                "exit_code": record.exit_code,
                "stdout": _truncate(stdout, 50000),
                "stdout_chars": len(stdout),
                "stderr": _truncate(stderr, 50000),
                "stderr_chars": len(stderr),
                "output": _truncate(output_text, 50000),
                "output_chars": len(output_text),
                "error": record.error,
                "metadata": metadata,
                "bound_inputs_preview": metadata.get("bound_inputs_preview"),
            }
        )
    return "\n".join(
        [
            *prompt_lines("operator.step_validation", {"mode_label": mode_label}),
            *_operator_streaming_step_lines(user_request),
            *_operator_execution_shape_lines(user_request),
            *_operator_gateway_context_lines(user_request),
            *_operator_policy_note_lines(user_request),
            "OperatorStepValidationReview schema:",
            _stable_json(OperatorStepValidationReview.model_json_schema()),
            "Original user prompt:",
            contract.original_request or user_request.raw_prompt,
            "Current step contract:",
            contract.model_dump_json(indent=2),
            "Executed plan for this step:",
            plan.model_dump_json(indent=2),
            "Execution evidence for this step:",
            _stable_json({"records": records}),
            "Prior step outputs available as context:",
            _stable_json(
                _compact_streaming_prior_results(
                    dict(user_request.session_context or {}).get("operator_streaming_prior_results")
                )
            ),
            "Deterministic artifact and bound-value evidence:",
            _stable_json(artifact_evidence),
        ]
    )


def build_operator_followup_prompt(
    user_request: UserRequest,
    conversation_context: dict[str, Any],
    feedback: list[dict[str, Any]] | None = None,
    rejected_plan: OperatorPlan | None = None,
) -> str:
    """Build a context-aware operator follow-up prompt."""

    lines = [
        *prompt_lines("operator.followup"),
        *_operator_discoverability_lines(),
        *memory_prompt_lines_from_context(user_request, stage="followup"),
        *parameter_prompt_lines_from_context(user_request, stage="followup"),
        *_operator_clarification_lines(user_request),
        *_operator_policy_note_lines(user_request),
        *_operator_self_brief_lines(user_request),
        *_operator_execution_shape_lines(user_request),
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
        "OperatorFollowupDecision schema:",
        _stable_json(OperatorFollowupDecision.model_json_schema()),
        "Conversation context JSON:",
        _stable_json(conversation_context),
    ]
    if feedback:
        lines.extend(
            [
                "Previous follow-up validation feedback:",
                _stable_json(feedback),
            ]
        )
        if rejected_plan is not None:
            lines.extend(
                [
                    "Rejected follow-up plan:",
                    rejected_plan.model_dump_json(indent=2),
                ]
            )
        lines.extend(
            [
                "Repair the follow-up decision once. If planning actions, fix only invalid fields.",
            ]
        )
    lines.extend(["User follow-up prompt:", user_request.raw_prompt])
    return "\n".join(lines)


__all__ = [name for name in globals() if not name.startswith("__")]
