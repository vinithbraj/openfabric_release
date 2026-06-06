"""Context and reusable line builders for operator prompts."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.operator.prompt_support.common import *

def _memory_directives_for_stage(user_request: UserRequest, stage: str) -> list[dict[str, Any]]:
    from agent_runtime.operator.memory_compliance import (
        _memory_directives_for_stage as memory_directives_for_stage,
    )

    return memory_directives_for_stage(user_request, stage)


def _operator_schema_prompt() -> str:
    return _stable_json(OperatorPlan.model_json_schema())


def _single_report_rejected_plan_summary(plan: OperatorPlan) -> str:
    """Summarize rejected report plans without feeding invalid code back to the model."""

    return _stable_json(
        {
            "summary": plan.summary,
            "task_count": len(plan.tasks),
            "action_count": len(plan.actions),
            "tasks": [
                {
                    "task_id": task.task_id,
                    "goal": task.goal,
                    "semantic_verb": task.semantic_verb,
                    "object_type": task.object_type,
                    "dependencies": list(task.dependencies or []),
                }
                for task in plan.tasks
            ],
            "actions": [
                {
                    "action_id": action.action_id,
                    "task_id": action.task_id,
                    "kind": action.kind,
                    "risk": action.risk,
                    "effect_intent": action.effect_intent,
                    "declared_output_shape": action.declared_output_shape,
                    "has_command": bool(str(action.command or "").strip()),
                    "has_code": bool(str(action.code or "").strip()),
                    "input_binding_count": len(action.input_bindings or []),
                    "depends_on": list(action.depends_on or []),
                }
                for action in plan.actions
            ],
        }
    )


def _operator_self_brief_from_request(user_request: UserRequest) -> OperatorSelfBrief | None:
    raw = dict(user_request.session_context or {}).get("operator_self_brief")
    if not isinstance(raw, dict):
        return None
    try:
        return OperatorSelfBrief.model_validate(raw)
    except Exception:
        return None


def _deliberation_frame_from_request(user_request: UserRequest) -> DeliberationFrame | None:
    raw = dict(user_request.session_context or {}).get("guided_deliberation_frame")
    if not isinstance(raw, dict):
        return None
    try:
        return DeliberationFrame.model_validate(raw)
    except Exception:
        return None


def _operator_self_brief_lines(user_request: UserRequest) -> list[str]:
    brief = _operator_self_brief_from_request(user_request)
    if brief is None:
        return []
    return [
        "Operator self-brief:",
        brief.model_dump_json(indent=2),
        "Use this brief as planning guidance. It is not execution output and does not replace validation, approval, or fresh verification.",
    ]


def _compact_streaming_prior_results(prior_results: Any) -> dict[str, Any]:
    """Return a prompt-sized summary of prior streaming task results."""

    if not isinstance(prior_results, dict):
        return {}
    compact_tasks: list[dict[str, Any]] = []
    for task in prior_results.get("completed_tasks") or []:
        if not isinstance(task, dict):
            continue
        compact_records: list[dict[str, Any]] = []
        for record in task.get("records") or []:
            if not isinstance(record, dict):
                continue
            compact_record: dict[str, Any] = {
                "action_id": str(record.get("action_id") or ""),
                "task_id": str(record.get("task_id") or ""),
                "kind": str(record.get("kind") or ""),
                "status": str(record.get("status") or ""),
                "exit_code": record.get("exit_code"),
            }
            stdout = str(record.get("stdout") or "").strip()
            stderr = str(record.get("stderr") or "").strip()
            output = record.get("output")
            error = str(record.get("error") or "").strip()
            if stdout:
                compact_record["stdout"] = _truncate(stdout, 1200)
            if stderr:
                compact_record["stderr"] = _truncate(stderr, 800)
            if output not in (None, ""):
                compact_record["output"] = _truncate(output, 1200)
            if error and error != stdout and error != stderr:
                compact_record["error"] = _truncate(error, 800)
            metadata = record.get("metadata")
            if isinstance(metadata, dict):
                cwd = str(metadata.get("cwd") or "").strip()
                if cwd:
                    compact_record["cwd"] = _truncate(cwd, 500)
                failure_metadata: dict[str, Any] = {}
                for key in (
                    "python_code_pre_execution_proof_failed",
                    "deferred_code_generation_failed",
                    "deferred_code_generation_errors",
                    "validation_errors",
                ):
                    if key in metadata:
                        failure_metadata[key] = metadata.get(key)
                proof = metadata.get("python_code_proof")
                if isinstance(proof, dict):
                    failure_metadata["python_code_proof"] = {
                        "decision": proof.get("decision"),
                        "reason": proof.get("reason"),
                        "proof_mode": proof.get("proof_mode"),
                        "failure_signature": proof.get("failure_signature"),
                        "errors": proof.get("errors"),
                        "stdout_preview": _truncate(proof.get("stdout_preview"), 800),
                        "stderr_preview": _truncate(proof.get("stderr_preview"), 800),
                    }
                generated_code = str(
                    metadata.get("generated_code")
                    or metadata.get("command")
                    or ""
                ).strip()
                if generated_code and (
                    failure_metadata
                    or str(record.get("kind") or "") in {"python_action", "python_transform"}
                ):
                    failure_metadata["generated_code_preview"] = _truncate(generated_code, 1200)
                if failure_metadata:
                    compact_record["failure_metadata"] = failure_metadata
            compact_records.append(compact_record)
        compact_task: dict[str, Any] = {
            "task_id": str(task.get("task_id") or ""),
            "description": _truncate(task.get("description"), 500),
            "status": str(task.get("status") or ""),
            "records": compact_records,
        }
        final_response = str(task.get("final_response") or "").strip()
        if final_response:
            compact_task["final_response"] = _truncate(final_response, 500)
        compact_tasks.append(compact_task)
    return {"completed_tasks": compact_tasks} if compact_tasks else {}


def _operator_streaming_step_lines(user_request: UserRequest) -> list[str]:
    context = dict(user_request.session_context or {})
    current_task = context.get("operator_streaming_current_task")
    if not isinstance(current_task, dict):
        return []
    execution_shape = execution_shape_from_request(user_request)
    prior_results = _compact_streaming_prior_results(
        context.get("operator_streaming_prior_results")
    )
    future_tasks: list[dict[str, Any]] = []
    tasks = context.get("operator_streaming_tasks")
    if isinstance(tasks, list):
        try:
            current_index = int(context.get("operator_streaming_current_index") or 0)
        except (TypeError, ValueError):
            current_index = 0
        for task in tasks[current_index + 1 :]:
            if not isinstance(task, dict):
                continue
            future_tasks.append(
                {
                    "task_id": str(task.get("task_id") or ""),
                    "description": str(task.get("description") or ""),
                    "semantic_verb": str(task.get("semantic_verb") or ""),
                    "object_type": str(task.get("object_type") or ""),
                }
            )
    original_prompt = str(context.get("streaming_original_prompt") or "").strip()
    intent_block = context.get("operator_intent_block")
    if not original_prompt and isinstance(intent_block, dict):
        constraints = intent_block.get("global_constraints")
        if isinstance(constraints, dict):
            original_prompt = str(constraints.get("streaming_original_prompt") or "").strip()
    lines = [
        "Streaming operator mode:",
        "The current decomposed task is the only executable scope for this call.",
        (
            "Treat the original full request as background context only; it does not "
            "authorize executing later steps early."
        ),
        (
            "Plan only the current decomposed task shown below. Do not include later "
            "decomposed tasks, even if the original request mentions them."
        ),
        (
            "For filter/select/sort/transform steps, produce only the selected or "
            "transformed targets from available evidence. Do not run, start, stop, "
            "delete, write, commit, push, install, or otherwise execute those targets "
            "unless the current task explicitly asks for that side effect."
        ),
        "The runtime will execute this task, record evidence, then call the operator again for the next decomposed task.",
        f"Original user request: {original_prompt or user_request.raw_prompt}",
        "Current decomposed task:",
        _stable_json(current_task),
    ]
    if execution_shape:
        lines.extend(
            [
                "Original request success contract:",
                _stable_json(execution_shape),
                "Even while focusing on this step, preserve typed obligations from the original request when validating output values.",
            ]
        )
    if future_tasks:
        lines.extend(
            [
                "Later decomposed tasks reserved for future calls:",
                _stable_json(future_tasks),
                "Do not author actions for these later tasks in the current plan.",
            ]
        )
    if prior_results:
        lines.extend(
            [
                "Prior streaming task outputs available as context for this step:",
                _stable_json(prior_results),
                "Use these prior outputs as evidence or targets for the current task. Do not rediscover the same targets unless the current task explicitly asks for fresh discovery.",
                "For calculate/summarize/filter/sort/transform steps over prior output, do not copy prior stdout/output into literal action inputs. Bind the prior action output with input_bindings, set defer_code_generation true, and set code to null so runtime code generation sees the actual input shape.",
                "For per-entity tasks such as for each/per/every/all entity reports over prior output, preserve the entity scope: bind the prior output or use an action that emits one row per entity. Do not answer a per-entity task with one global scalar selected by head -n 1, tail -n 1, or an equivalent first/last-only selector.",
                "If prior stdout contains relative paths, resolve them against that prior record's cwd and then use/report absolute paths. Do not resolve prior relative paths against the repo workspace unless the prior record cwd is the workspace.",
                "When aggregating repeated records, deduplicate by a stable entity identifier only when the user requested unique entities; otherwise preserve the requested counting semantics.",
            ]
        )
    return lines


def _operator_execution_shape_lines(user_request: UserRequest) -> list[str]:
    hint = execution_shape_from_request(user_request)
    if not hint:
        return []
    lines = [
        "Execution shape hint:",
        _stable_json(hint),
        "Treat this as diagnostic context only. It must not bypass decomposition, collapse tasks, or force a collapsed plan. Preserve safety and approval requirements.",
    ]
    contract = hint.get("report_contract") if isinstance(hint.get("report_contract"), dict) else {}
    if str(contract.get("scope") or "") == "all_entities":
        lines.append(
            "For all-entities reports, compute required fields for every requested entity; do not target only the current, selected, default, or first entity. If the discovered entity set is non-empty, the final report must emit rows for that set or fail clearly."
        )
    return lines


def _operator_prompt_intent_block(intent_block: Any) -> Any:
    """Return a prompt-sized intent block without duplicated streaming ledger payloads."""

    if not isinstance(intent_block, dict):
        return intent_block
    if str(intent_block.get("mode") or "") != "decomposition_streaming_step":
        return intent_block
    compact = dict(intent_block)
    constraints = compact.get("global_constraints")
    if isinstance(constraints, dict):
        compact_constraints = dict(constraints)
        compact_constraints.pop("streaming_prior_results", None)
        compact["global_constraints"] = compact_constraints
    return compact


def _operator_gateway_context_lines(user_request: UserRequest) -> list[str]:
    context = dict(user_request.session_context or {})
    gateway_keys = [
        "gateway_id",
        "gateway_node",
        "gateway_url",
        "gateway_platform",
        "gateway_platform_label",
        "gateway_platform_version",
        "gateway_architecture",
        "gateway_shell",
        "gateway_command_profile",
        "gateway_capability_tags",
    ]
    payload = {
        key: context.get(key)
        for key in gateway_keys
        if context.get(key) not in (None, "", [], {})
    }
    if not payload:
        return []
    platform = str(payload.get("gateway_platform") or "").strip().lower()
    if platform == "macos":
        platform_guidance = (
            "Target platform is macOS. Prefer macOS-native CLI tools when appropriate, "
            "including osascript, shortcuts, open, pbcopy, pbpaste, screencapture, mdfind, "
            "system_profiler, networksetup, pmset, launchctl, and defaults. Do not assume GNU "
            "Linux-only flags or Linux package/service paths. Use runtime discovery when a "
            "command differs across macOS versions."
        )
    elif platform == "linux":
        platform_guidance = (
            "Target platform is Linux. Prefer portable POSIX shell where possible and use "
            "Linux-specific commands only when the request or discovered runtime state calls for them."
        )
    else:
        platform_guidance = (
            "Target gateway platform is unknown. Prefer portable POSIX shell and discover OS-specific "
            "facts before using platform-specific commands."
        )
    return prompt_lines(
        "operator.gateway_context",
        {
            "gateway_context_json": _stable_json(payload),
            "platform_guidance": platform_guidance,
        },
    )


def _guided_deliberation_lines(user_request: UserRequest) -> list[str]:
    frame = _deliberation_frame_from_request(user_request)
    if frame is None:
        return []
    return [
        "Guided deliberation frame:",
        frame.model_dump_json(indent=2),
        "Use this concise frame to keep the plan aligned with the user's goal, observed evidence needs, risks, and success criteria. It is not execution output and cannot override validation, approval, hard safety policy, or live evidence.",
    ]


def _operator_clarification_lines(user_request: UserRequest) -> list[str]:
    """Return request-scoped user clarification answers for LLM prompts."""

    raw = dict(user_request.session_context or {}).get("clarifications")
    if not isinstance(raw, list) or not raw:
        return []
    safe_entries: list[dict[str, Any]] = []
    for entry in raw[-5:]:
        if not isinstance(entry, dict):
            continue
        safe_entries.append(
            {
                "question": _truncate(entry.get("question"), 500),
                "answer": _truncate(entry.get("answer"), 500),
                "selected_option_id": str(entry.get("selected_option_id") or ""),
                "reason": _truncate(entry.get("reason"), 500),
                "missing_information": _truncate(entry.get("missing_information"), 500),
                "timestamp": str(entry.get("timestamp") or ""),
            }
        )
    if not safe_entries:
        return []
    return [
        "User clarification answers already provided:",
        _stable_json(safe_entries),
        "Treat these clarification answers as explicit user constraints. Do not ask the same question again.",
        "If a clarification answer authorizes a previously missing execution mode, privilege, target, or option, the next plan or repair must materially incorporate that answer instead of replaying the earlier failed command unchanged.",
    ]


def _operator_policy_note_lines(user_request: UserRequest) -> list[str]:
    """Return runtime-enforced policy notes for the next LLM decision."""

    raw = dict(user_request.session_context or {}).get("operator_policy_notes")
    if not isinstance(raw, list) or not raw:
        return []
    safe_notes: list[dict[str, Any]] = []
    for entry in raw[-5:]:
        if not isinstance(entry, dict):
            continue
        safe_notes.append(
            {
                "phase": _truncate(entry.get("phase"), 120),
                "reason": _truncate(entry.get("reason"), 500),
                "instruction": _truncate(entry.get("instruction"), 1000),
                "question": _truncate(entry.get("question"), 500),
                "missing_information": _truncate(entry.get("missing_information"), 500),
            }
        )
    if not safe_notes:
        return []
    return [
        "Runtime-enforced operator policy notes:",
        _stable_json(safe_notes),
        "These notes are authoritative. Follow them before asking another clarification.",
    ]


def _operator_discoverability_lines() -> list[str]:
    """Return general guidance for facts the runtime can safely discover itself."""

    return [
        "Runtime-discoverable context policy:",
        "Do not ask the user for local facts the runtime can safely inspect with read-only commands or Python.",
        "Words like current, active, this, here, local, installed, available, existing, or default usually refer to runtime state that should be discovered, not clarified.",
        "Examples of discoverable facts include current git branch, current cwd, existing files/folders, installed conda environments, Docker images/containers, attached devices/drives, mountpoints, available disk/memory, local CPU/GPU make/model, hardware/device vendor info, and command versions.",
        "For read-only hardware identity requests, inspect the local machine and report the observed CPU/GPU/device names or vendors; if multiple devices exist, report them all or label them instead of asking which one.",
        "For requests like push, pull, remove matching items, or update the current/default target, inspect the relevant current/default runtime state first instead of asking the user to restate it.",
        "Ask the user only for preferences or intent that cannot be discovered, such as a desired version to create, which of several valid targets to modify, credentials, budget, or irreversible choices.",
        "For create/new/setup/configure requests, desired names, versions, templates, sizes, scopes, and preferences are user-choice values, not runtime-discoverable facts. Ask if they are omitted and materially affect the result.",
        "Do not substitute the current installed/runtime version for a desired creation version unless the user explicitly says to use current, default, or installed values.",
        "When a discoverable fact is needed for a later mutation, first plan a read-only inspection/probe, then use its fresh result or recompute it inside the mutating action.",
    ]


def _operator_user_hint_lines() -> list[str]:
    """Return general guidance for asking the user for a hint when progress is blocked."""

    return [
        "User-hint fallback policy:",
        "If safe discoverable probes and live stdout/stderr/error evidence are insufficient, and the next safe step depends on domain knowledge, user preference, credentials, or a tool-specific choice that is not in the prompt or prior clarifications, ask one concise user-facing clarification for a hint instead of looping or repeating the same failed action.",
        "Do not use this fallback to ask for runtime-discoverable local facts; inspect those with read-only commands or Python first.",
    ]


def _operator_user_macro_lines(user_request: UserRequest) -> list[str]:
    """Return deterministic macro guidance when the user supplied macros."""

    summaries = [
        item
        for item in user_macro_summaries_from_context(user_request.session_context)
        if str(item.get("kind") or "").strip().lower() not in {"checkonline", "checkonlineai"}
    ]
    if not summaries:
        return []
    deterministic_summaries = [
        item
        for item in summaries
        if str(item.get("kind") or "").strip().lower() != "slash_macro_candidate"
    ]
    slash_candidates = [
        item
        for item in summaries
        if str(item.get("kind") or "").strip().lower() == "slash_macro_candidate"
    ]
    lines = ["Deterministic user macro policy:"]
    if deterministic_summaries:
        deterministic_lines = [
            "The user supplied one or more deterministic macros. Treat them as already-provided input, not as text to inline into commands and not as something to ask for again.",
            "The macro value itself is private and unavailable to you; only the runtime can deliver it during execution.",
            "For terminal prompts, plan a shell_command with interaction_mode may_prompt so the runtime can type the macro value into the active terminal when input is requested.",
            "For captured non_interactive commands that explicitly read stdin, set stdin_mode to input_binding and stdin_input_name to the macro input_name; leave input_bindings empty for reserved user macro stdin.",
            "Do not create normal shell inputs or input_bindings such as password, passphrase, secret, token, commit_message, or message for a deterministic macro. The macro is not an OF_INPUT_* shell input unless stdin_input_name is exactly the listed macro input_name.",
        ]
        if request_authorizes_sudo(user_request):
            deterministic_lines.append(
                "When the user explicitly authorized sudo and a typein/parameter macro is present, author sudo work as shell_command with interaction_mode may_prompt, stdin_mode none, and no password, passphrase, secret, or token shell inputs; the runtime delivers the macro only to the terminal prompt."
            )
        lines.extend(
            [
                *deterministic_lines,
                "Available deterministic macros:",
                _stable_json(deterministic_summaries),
            ]
        )
    if slash_candidates:
        lines.extend(
            [
                "Standalone slash-token policy:",
                "The user included standalone slash-prefixed token(s). Treat these as macro/control-token candidates rather than ordinary payload text.",
                "If a slash token is not a recognized deterministic macro, preserve the user's requested task intent but do not invent shell inputs, credentials, commit messages, paths, or command arguments from that token.",
                "Detected slash-token candidates:",
                _stable_json(slash_candidates),
            ]
        )
    return lines


def _operator_online_lookup_lines(user_request: UserRequest) -> list[str]:
    """Return compact online lookup context when online lookup macros were requested."""

    return [
        *online_lookup_prompt_lines(user_request.session_context),
        *online_ai_check_prompt_lines(user_request.session_context),
    ]


def _operator_literal_payload_lines(user_request: UserRequest) -> list[str]:
    """Return user-provided literal payloads shielded from decomposition."""

    return literal_payload_prompt_lines(user_request.session_context)


def _literal_payload_replacements_from_request(user_request: UserRequest) -> dict[str, dict[str, Any]]:
    """Return runtime-owned literal payload replacements keyed by placeholder."""

    replacements: dict[str, dict[str, Any]] = {}
    for context in (user_request.session_context, user_request.safety_context):
        for payload in literal_payloads_from_context(context):
            placeholder = str(payload.get("placeholder") or "").strip()
            if not placeholder:
                continue
            replacements.setdefault(
                placeholder,
                {
                    "payload_id": str(payload.get("payload_id") or ""),
                    "kind": str(payload.get("kind") or ""),
                    "input_name": str(payload.get("input_name") or ""),
                    "placeholder": placeholder,
                    "value": str(payload.get("value") or ""),
                    "value_length": int(payload.get("value_length") or len(str(payload.get("value") or ""))),
                },
            )
    return replacements


def _replace_literal_payload_placeholders(
    value: Any,
    replacements: dict[str, dict[str, Any]],
) -> tuple[Any, bool]:
    """Replace known literal payload placeholders inside data values only."""

    if isinstance(value, str):
        updated = value
        for placeholder, payload in replacements.items():
            if placeholder in updated:
                updated = updated.replace(placeholder, str(payload.get("value") or ""))
        return updated, updated != value
    if isinstance(value, list):
        changed = False
        items: list[Any] = []
        for item in value:
            updated, item_changed = _replace_literal_payload_placeholders(item, replacements)
            changed = changed or item_changed
            items.append(updated)
        return items, changed
    if isinstance(value, dict):
        changed = False
        updated_dict: dict[str, Any] = {}
        for key, item in value.items():
            updated, item_changed = _replace_literal_payload_placeholders(item, replacements)
            changed = changed or item_changed
            updated_dict[str(key)] = updated
        return updated_dict, changed
    return value, False


def _literal_payload_placeholder_matches(value: Any) -> list[str]:
    """Return placeholder sentinels still present inside one value."""

    matches: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, str):
            matches.extend(match.group(0) for match in _LITERAL_PAYLOAD_PLACEHOLDER_RE.finditer(item))
            return
        if isinstance(item, dict):
            for nested in item.values():
                visit(nested)
            return
        if isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    seen: set[str] = set()
    unique: list[str] = []
    for match in matches:
        if match in seen:
            continue
        seen.add(match)
        unique.append(match)
    return unique


def _operator_absolute_path_lines() -> list[str]:
    """Return path handling policy shared by operator prompts."""

    return [
        "Absolute path policy:",
        "When an action discovers, lists, reports, or passes file/directory paths to another action, produce absolute paths.",
        "For shell filesystem discovery, prefer absolute search roots such as `find \"$PWD\" ...`, `find /absolute/root ...`, or pipe relative output through `realpath`/`readlink -f`.",
        "Do not emit `find . ...` or `rg --files` output as final path evidence unless the command converts the paths to absolute form.",
        "For Python filesystem discovery, resolve paths with pathlib.Path(...).expanduser().resolve(strict=False) before printing, returning, or passing them downstream.",
        "When consuming prior relative path output, resolve it against the producing record's cwd first, then use/report the absolute path.",
    ]


def _operator_verification_bias_lines() -> list[str]:
    """Return shared guidance for exact, stable postcondition verification."""

    return [
        "Verification evidence policy:",
        "Verify the exact user-requested postcondition and self-brief verification contract; do not make the verifier stricter, broader, or different than the requested end state.",
        "Use fresh state evidence for mutations and treat the requested resulting state as success; do not add negative checks that reject the state the user asked to create or preserve.",
        "Prefer machine-readable state commands, stable output flags, or APIs over narrative human prose when proving completion.",
        "Do not grep human-oriented command prose when a stable --porcelain, --json, --format, explicit-column, or API-style state check is available.",
        "When a verifier is flawed, revise toward a simpler stable state check; do not layer more prose-grep conditions onto the same brittle verifier.",
    ]


def _operator_verification_enforced_from_request(user_request: UserRequest) -> bool:
    """Return whether this request should require explicit verification steps."""

    return bool(
        dict(user_request.session_context or {}).get(
            "llm_operator_verification_enforced",
            True,
        )
    )


def _operator_verification_mode_lines(*, verification_enforced: bool) -> list[str]:
    """Return request-scoped verification guidance."""

    if verification_enforced:
        return [
            "Verification mode: enforced.",
            "For any mutating action, verify the side effect before reporting success. Either perform verification inside the same action or add a downstream read-only verification action that confirms the requested state changed.",
            "For read-only listing, counting, summarization, and computed-answer requests, the fresh command/Python action that directly produces the requested rows or value is sufficient verification when it validates parsing and fails on missing or unparsable data; do not add a separate verification action just to re-prove the same computed value.",
            "Do not print or return 'removed', 'created', 'updated', 'committed', 'pushed', or similar success claims unless the action captured a successful exit status and verified the resulting state.",
            "For conditional mutations based on list/search output, handle the no-target case explicitly as a no-op result, and for matched targets report exactly which targets were verified after mutation.",
            *_operator_verification_bias_lines(),
            "A verification action must inspect fresh post-mutation state itself, or depend on a fresh read action that runs after the mutation. Never verify a mutation from the pre-mutation list/search output.",
            "Verification commands must encode success and failure criteria explicitly. If the requested postcondition is unmet, the verifier must print a clear failure message and exit nonzero.",
            "Verification commands must be executable as written. Do not use placeholder output like <calculated_value>, do not refer to imaginary files, and do not assume shell actions can read previous action output unless they recompute it inside the same command.",
            "For absence verification, success means the target is absent and failure means the target remains; encode both branches explicitly instead of relying on a naked search command exit status.",
        ]
    return [
        "Verification mode: relaxed.",
        "The user has disabled enforced verification for this request. Do not add separate verification actions solely to prove completion unless the user explicitly asked for verification, the action itself requires a result check to avoid false success, or the verification output is needed to answer the request.",
        "For shell_command actions, non-empty stdout is treated as useful command output and can be sufficient evidence; when stdout is empty, stderr and exit status determine failure. Python actions must still raise on real command failures.",
        "For read-only listing, counting, summarization, and computed-answer requests, the fresh command/Python result is sufficient evidence when it directly produces the requested answer.",
    ]


def _operator_contract_lines(
    default_cwd: str | None = None,
    *,
    terminal_execution_enabled: bool = False,
    defer_upstream_python: bool = True,
    verification_enforced: bool = True,
    shell_input_bindings_mode: str = "allow",
) -> list[str]:
    """Return compact operator action rules shared by plan and repair prompts."""

    cwd_rule = (
        f"An Agent UI terminal is active. Use cwd {default_cwd!r} for operator actions unless the user explicitly requests another cwd."
        if default_cwd
        else "Use cwd '.' unless the user explicitly requests a workspace-relative subdirectory."
    )
    python_timing_rules = (
        [
            "For any python_action or python_transform that consumes previous action output, set defer_code_generation true and set code to null.",
            "Deferred Python actions must declare input_bindings; the runtime will ask for exact Python code only after upstream stdout/stderr/output previews are known.",
            "Do not guess upstream columns, units, JSON shape, table layout, or text format while planning; defer the Python code instead.",
        ]
        if defer_upstream_python
        else [
            "This prompt includes live execution records. When repairing a failed python_action or python_transform, author concrete corrected code against the shown stdout/stderr/output and traceback.",
            "Do not defer repaired Python code when the runtime input shape is present in this prompt.",
            "Use defer_code_generation only for newly introduced Python actions whose required upstream data is still unavailable.",
        ]
    )
    shell_binding_mode = _shell_input_bindings_mode_from_value(shell_input_bindings_mode)
    if shell_binding_mode == "off":
        shell_binding_rules = [
            "input_bindings are valid on python_action and python_transform actions; shell_command input_bindings must be [].",
            "If a python_action or python_transform needs an earlier action result, declare input_bindings on that action.",
            "Shell commands do not receive runtime interpolation from input_bindings. If shell work needs a discovered value, compute and use it inside the same shell command with local shell assignment.",
            "Concrete shell pattern: value=$(producer_command | filter_command | head -n 1); consumer_command \"$value\".",
        ]
    elif shell_binding_mode == "confirm_bound":
        shell_binding_rules = [
            "Shell input bindings mode: confirm_bound. shell_command actions may consume earlier action outputs through input_bindings, but the runtime will pause for a second confirmation after bound values are known.",
            "For each shell input_binding, reference the runtime-provided environment variable named OF_INPUT_<UPPERCASE_INPUT_NAME>, for example input_name commit_hash must be used as \"$OF_INPUT_COMMIT_HASH\" or \"${OF_INPUT_COMMIT_HASH}\".",
            "If a later shell command uses an ID, path, SQL scalar, selected row, count, total, generated text, or other value produced by an earlier action, declare an input_binding from that producer; do not paste the discovered value into command text or literal inputs.",
            "Do not use placeholders such as <commit_hash>, {commit_hash}, {{commit_hash}}, $COMMIT_HASH, or ${COMMIT_HASH}; only OF_INPUT_* variables backed by declared input_bindings are allowed.",
            "Shell input bindings are for short single-line stdout/output/exit_code values from earlier actions; do not bind stderr, multiline blobs, or large payloads into shell.",
            "For multiline/table upstream output, add a Python transform that extracts a short value before shell reuse, or use stdin_mode=input_binding when the shell command intentionally reads the whole payload.",
        ]
    else:
        shell_binding_rules = [
            "Shell input bindings mode: allow. shell_command actions may consume earlier action outputs through input_bindings without a second confirmation.",
            "For each shell input_binding, reference the runtime-provided environment variable named OF_INPUT_<UPPERCASE_INPUT_NAME>, for example input_name commit_hash must be used as \"$OF_INPUT_COMMIT_HASH\" or \"${OF_INPUT_COMMIT_HASH}\".",
            "If a later shell command uses an ID, path, SQL scalar, selected row, count, total, generated text, or other value produced by an earlier action, declare an input_binding from that producer; do not paste the discovered value into command text or literal inputs.",
            "Do not use placeholders such as <commit_hash>, {commit_hash}, {{commit_hash}}, $COMMIT_HASH, or ${COMMIT_HASH}; only OF_INPUT_* variables backed by declared input_bindings are allowed.",
            "Shell input bindings are for short single-line stdout/output/exit_code values from earlier actions; do not bind stderr, multiline blobs, or large payloads into shell.",
            "For multiline/table upstream output, add a Python transform that extracts a short value before shell reuse, or use stdin_mode=input_binding when the shell command intentionally reads the whole payload.",
        ]
    return [
        "You may use only action kinds: shell_command, python_action, python_transform, llm_text.",
        "For shell_command actions, provide command, cwd, execution_mode, interaction_mode, inputs, input_bindings, stdin_mode, stdin_text, stdin_input_name, declared_output_shape, risk, timeout_seconds, effect_intent, effect_confidence, effect_summary, and reason.",
        "For llm_text actions, provide llm_prompt, inputs, input_bindings, declared_output_shape text, effect_intent read_only, risk low, and reason. Do not provide command, code, stdin fields, may_prompt, or terminal_detached.",
        "For every action, set effect_intent to read_only, mutates_state, or unknown; set effect_confidence from 0.0 to 1.0; and write a concise effect_summary grounded in the actual command, code, or llm_prompt.",
        "In LLM-owned policy mode, these typed effect fields are policy inputs. Be conservative: state-changing, external-system-changing, package/service/repo/container/database, file-write, environment-create/remove, and process-control actions are mutates_state.",
        "Within the current decomposed task, use one shell_command when a single concrete command safely completes that task.",
        "When the user asks to generate, draft, compose, write, or summarize natural-language text from runtime evidence, use llm_text for the generated text itself.",
        "Shell commands may collect evidence for generated text, but evidence-only shell stdout does not satisfy a generated-text step.",
        "Do not bind llm_text to a prior action whose stdout/output is empty; collect fresh read-only evidence first.",
        "When a later step uses previously generated text, bind the prior llm_text stdout/output instead of adding another llm_text action.",
        "Later shell actions that consume generated text must use input_bindings from the llm_text action. Use stdin_mode=input_binding for multiline generated text; use OF_INPUT_* only for short single-line generated values.",
        "For Git commits that consume a multiline generated commit message, use command `git commit -F -` with stdin_mode=input_binding bound to the llm_text output. Do not use `git commit -m \"$OF_INPUT_*\"` for multiline messages, and do not invent temp-file paths for generated text.",
        "If a mutating action needs natural-language text and the user did not provide it or ask you to generate it, ask for clarification instead of inventing a default message, description, title, subject, body, or summary.",
        "For parsing, aggregation, or transforms over prior evidence, prefer python_action: code must start exactly with def main(inputs): as the first non-whitespace source text, with all imports and executable work inside that function body.",
        "python_action may use Python stdlib, subprocess, os, pathlib, glob, and data manipulation to produce the final answer in one gateway-executed action.",
        "For python_action, print or return the final user-facing result from main(inputs); do not split command execution and parsing into separate actions unless prior action data is genuinely needed.",
        "When Python iterates over a list or discovered entities, handle exceptions inside each iteration, append item-level error details with the item identifier to an errors collection, and continue processing remaining items; after the loop, raise only if the requested overall result has no successful rows/items or required fields are missing.",
        "Every action.task_id must exactly match a task_id in tasks; every dependency, depends_on entry, and input_binding source_action_id must exactly match an action_id in actions.",
        "Use interaction_mode non_interactive for commands that complete without user input.",
        "Use interaction_mode may_prompt for finite shell commands that may require a real terminal, passphrase, password, confirmation prompt, credential prompt, or controlling TTY.",
        "Do not wrap may_prompt work in python_action or Python subprocess capture; author it as a shell_command so the runtime can attach it to the terminal PTY.",
        "For user-provided literal values in shell_command actions, put them in inputs and reference the runtime-provided environment variable named OF_INPUT_<UPPERCASE_INPUT_NAME> inside command, for example inputs.message_payload must be used as \"$OF_INPUT_MESSAGE_PAYLOAD\" or \"${OF_INPUT_MESSAGE_PAYLOAD}\".",
        "Shell inputs are function-call arguments for shell: every shell_command inputs key must be consumed through its matching OF_INPUT_* variable. Do not inline user-provided text into shell quotes.",
        "Captured non_interactive shell stdin is closed unless explicitly declared. Prefer shell inputs plus an explicit pipe/file inside the command over stdin_mode for user-provided payloads.",
        "Do not emit bare stdin-reading commands such as `command -`, `tool --input -`, `cat > file`, or `read var` with stdin_mode none.",
        "Use interaction_mode long_running only with terminal_detached shell commands that the user should observe/control in the terminal.",
        "Use execution_mode captured for normal commands whose output or exit code is needed by the answer or later actions.",
        "Do not run foreground service commands in captured non_interactive mode. For service startup that should complete, use a detach/background flag such as docker compose up -d; if the user explicitly wants to observe foreground output, use terminal_detached long_running.",
        (
            "Use execution_mode terminal_detached only for long-running interactive terminal commands such as watch, tail -f, htop, top, REPLs, or commands the user wants to observe/control in the terminal."
            if terminal_execution_enabled
            else "Do not use execution_mode terminal_detached unless Run in Terminal is enabled for this request."
        ),
        "terminal_detached actions require an active terminal, do not produce dataflow output, must not have downstream dependencies, and should use declared_output_shape interactive_stream or status.",
        "may_prompt shell actions are finite terminal-capable commands; they do not require a terminal at planning time, but the runtime will route them through a trusted terminal when approved if they may need user input.",
        "Use risk low/medium for read-only inspection commands; use risk high/critical for commands that create, update, delete, stop, remove, or otherwise mutate state.",
        *_operator_verification_mode_lines(verification_enforced=verification_enforced),
        *_operator_absolute_path_lines(),
        "Prefer simple well-known CLI invocations over invented shorthand flags; when exact calculation is needed and shell alone is awkward, use python_action.",
        "Distinguish file contents from commands to execute. If you need to write source/config text, make it clear that placeholder-like syntax is literal file payload content.",
        "Do not compile, run, lint, or test generated source files unless the user asked for that execution or verification is needed for the requested outcome.",
        "Prefer python_action file writes or clear heredoc/tee shell actions for creating files with literal payloads.",
        "If a shell command needs a path or value discovered by search/list output, recompute that lookup inside the same concrete command; do not assume a bare basename is in the current directory.",
        "When output will be parsed, filtered, counted, transformed, joined, or used to select a target, prefer machine-readable CLI output such as --format, --json, --porcelain, -o/--output, or explicit columns when supported.",
        "For exact arithmetic over human-readable units such as KB/MB/GB, KiB/MiB/GiB, disk usage, or memory sizes, use python_action/python_transform with explicit unit conversion unless the CLI can emit canonical numeric units such as bytes or nounits.",
        "Do not guess unfamiliar command flags. When exact CLI syntax/options are uncertain, first use a read-only help discovery action such as `<command> --help`, `<command> -h`, `<command> help`, or an equivalent manual/usage probe, then use that evidence for the real command.",
        "When parsing compact size strings such as 119MB or 2.43GB, split the numeric prefix from the alphabetic suffix or check longer suffixes like TB/GB/MB/KB before bare B; never strip the final B before deciding the full unit.",
        "If using raw Python regex, do not double-escape regex classes: use r'\\d+' rather than r'\\\\d+'. For compact number+unit tokens, simple character splitting is usually safer than regex.",
        "For repository or codebase searches, default to rg/rg --files when available and exclude noisy generated/cache directories such as .git, .venv, __pycache__, node_modules, dist, build, .pytest_cache, and site-packages unless the user explicitly asks to include them.",
        "For Docker Compose, put global compose options before the subcommand: use `docker compose -f <file> up -d`, not `docker compose up -d --file <file>`.",
        "Do not parse human-readable tables with fixed awk column numbers unless no structured output exists; for destructive commands, select targets by stable ids or exact names from structured output.",
        "For yes/no existence checks, print an explicit found/not-found answer and exit 0 for both outcomes; do not let a no-match status become a command failure.",
        "For name matching, use exact matching only when the user explicitly asks for an exact full name; otherwise prefer case-insensitive fixed substring matching.",
        "For python_transform actions, code must start with def transform(inputs): and should consume declared inputs; use python_action when one Python program should own command execution plus transformation.",
        *python_timing_rules,
        "Put imports inside the Python function body, never before it, so the source starts with the required def line.",
        "If Python code uses a module name such as re, json, math, os, pathlib, glob, subprocess, pandas, numpy, or yaml, import it inside the function before first use.",
        "Operator Python is trusted local execution like shell: subprocess, filesystem APIs, imports, and normal data-processing libraries are permitted.",
        "Prefer python_action for filesystem discovery, globbing, directory walks, stat calls, subprocesses, and environment inspection because it executes through the gateway path.",
        "JSON-first output preference: when a tool or CLI can emit native JSON or JSON-template output, prefer that over human/table output for producer actions.",
        "Set declared_output_shape to json whenever the action stdout/output is JSON. Use text, status, or table only when JSON is unavailable, unsafe, less faithful, or the user explicitly requests raw/plain/table output.",
        "For Docker read-only inspection and listing, prefer documented JSON-capable forms such as docker inspect default JSON, --format json where supported, or Go-template JSON like --format '{{json .}}'; docker compose ps/images --format json are canonical examples.",
        "For generated Python outputs, prefer JSON-serializable dict/list values with named fields such as rows, items, total, summary, and errors instead of ad hoc prose strings.",
        "For parsing or aggregation transforms, never return 0, an empty table, or a success string when required inputs are missing or unparsable; raise ValueError with a precise message.",
        "For aggregation from rows, track how many values were actually parsed; if non-empty input yields zero parsed values, raise ValueError instead of returning a zero aggregate.",
        "For total/size aggregation inside a self-contained action, track parsed value count from the discovered source output; a zero total must be backed by an explicitly empty source set, not by a parser that matched nothing.",
        "For required report fields, do not write code paths that substitute Unknown, N/A, null, blank, or not available. Raise a clear error instead.",
        "For required report fields, do not set field variables to None/null and do not silently continue after a source command fails or parsing produces no values; raise a clear error with the failing command's stderr/stdout preview.",
        "If a python_action or python_transform can legitimately return numeric zero from non-empty required inputs, set allow_zero_result true and explain why in reason.",
        *shell_binding_rules,
        "Each input_binding must use this exact canonical shape: input_name, source_action_id, source_field, required, fallback_value.",
        "Use source_field stdout for shell stdout. Do not use aliases such as output_name, output_key, source_task_id, or producer_action_id.",
        "An action cannot bind inputs from itself. For a one-action Python solution, omit input_bindings and put literal inputs in inputs or gather data inside the action.",
        "Example: input_bindings: [{\"input_name\":\"disk_free_kb\",\"source_action_id\":\"action_1\",\"source_field\":\"stdout\"}].",
        "Then Python code may read inputs['disk_free_kb']; do not assume prior outputs are injected unless input_bindings declares them.",
        "If you repair or rewrite a plan, return the whole corrected OperatorPlan and preserve task_id/action_id values unless you update every reference consistently.",
        "Shell commands must be concrete at approval time. Do not emit unresolved placeholders such as {branch_name}, {{ branch_name }}, <calculated_value>, $BRANCH_NAME, or ${BRANCH_NAME}.",
        "Double-brace tool syntax with a dot is allowed, such as Docker --format '{{.Names}}'; runtime placeholders like {{container_name}} are not allowed.",
        "Bad: docker inspect {{container_name}}. Good: name=$(docker ps --format '{{.Names}}' | awk 'tolower($0) ~ /pattern/ {print; exit}'); test -n \"$name\" && docker inspect \"$name\".",
        "Validation repair guidance: if blocked syntax is literal file payload content, rewrite the action to make that intent clear; if it is a real placeholder, replace it with concrete shell lookup or Python inputs.",
        "Python transform pattern: def transform(inputs): stdout = str(inputs['stdout']); "
        "if not stdout.strip(): raise ValueError('Required stdout input is empty.'); return stdout.strip(). "
        "Prefer direct inputs['stdout'] access for required bound inputs so missing upstream data fails clearly. "
        "Captured Python should use inputs or subprocess output rather than interactive stdin.",
        cwd_rule,
    ]


def _terminal_cwd_from_request(user_request: UserRequest) -> str | None:
    for context in (user_request.session_context, user_request.safety_context):
        value = str(dict(context or {}).get("terminal_cwd") or "").strip()
        if value:
            return value
    return None


def _terminal_execution_enabled_from_request(user_request: UserRequest) -> bool:
    for context in (user_request.session_context, user_request.safety_context):
        if bool(dict(context or {}).get("execute_in_terminal")):
            return True
    return False


def _operator_fast_trout_evidence_lines(user_request: UserRequest) -> list[str]:
    records = user_request.session_context.get("operator_fast_trout_records")
    if not records:
        return []
    lines = [
        "Retired tryout evidence already gathered for this request:",
        _stable_json(records),
        "Use this as fresh runtime evidence. Do not repeat those exact actions unless the user goal still requires it.",
    ]
    review_errors = user_request.session_context.get("operator_fast_trout_result_review_errors")
    if review_errors:
        lines.extend(
            [
                "Retired tryout result review issues to address:",
                _stable_json(review_errors),
            ]
        )
    return lines


__all__ = [name for name in globals() if not name.startswith("__")]
