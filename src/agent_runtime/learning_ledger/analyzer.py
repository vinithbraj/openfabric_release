"""Deterministic learning-ledger analyzer for completed UI runs."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from agent_runtime.learning_ledger.models import (
    CapabilityInsightWrite,
    CapabilityProposalWrite,
    LearningActionWrite,
    LearningCacheEventWrite,
    LearningLessonWrite,
    LearningOutcome,
    LearningRunWrite,
)
from agent_runtime.learning_ledger.store import AgentLearningLedgerStore
from agent_runtime.memory.store import normalize_model_family
from agent_runtime.observability.agent_trace import AgentRequestTrace


_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|passwd|authorization|bearer)\s*[:=]\s*\S+"
)


def prompt_signature(prompt: str) -> str:
    """Return a stable shape-ish signature for a user prompt."""

    text = " ".join(str(prompt or "").lower().split())
    text = re.sub(r"\"[^\"]*\"|'[^']*'|`[^`]*`", "<literal>", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "<number>", text)
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def hash_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def safe_text(value: Any, *, limit: int = 500) -> str:
    text = " ".join(str(value or "").split()).strip()
    text = _SECRET_RE.sub("<redacted>", text)
    return text[:limit]


def _metadata(planning_trace: Any) -> dict[str, Any]:
    raw = getattr(planning_trace, "metadata", None)
    return dict(raw) if isinstance(raw, dict) else {}


def _records_from_metadata(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in list(metadata.get("operator_execution_records") or [])
        if isinstance(item, dict)
    ]


def _validation_errors_from_metadata(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for key in ("operator_validation_errors", "validation_errors"):
        for item in list(metadata.get(key) or []):
            if isinstance(item, dict):
                errors.append(dict(item))
    return errors


def _capability_gaps_from_trace(metadata: dict[str, Any], planning_trace: Any) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    raw_trace_gaps = getattr(planning_trace, "capability_gaps_by_task", None)
    if isinstance(raw_trace_gaps, dict):
        for task_id, value in raw_trace_gaps.items():
            if isinstance(value, dict):
                gaps.append({"task_id": str(task_id), **dict(value)})
    raw_meta_gaps = metadata.get("capability_gaps")
    if isinstance(raw_meta_gaps, list):
        gaps.extend(dict(item) for item in raw_meta_gaps if isinstance(item, dict))
    return gaps


def _event_detail(event: Any) -> dict[str, Any]:
    detail = getattr(event, "detail", None)
    return dict(detail) if isinstance(detail, dict) else {}


def _memory_ids(metadata: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for item in list(metadata.get("agent_memory_matches") or []):
        if not isinstance(item, dict):
            continue
        entry = item.get("entry")
        if isinstance(entry, dict) and str(entry.get("memory_id") or "").strip():
            ids.append(str(entry.get("memory_id")))
        elif str(item.get("memory_id") or "").strip():
            ids.append(str(item.get("memory_id")))
    for item in list(metadata.get("agent_memory_directives") or []):
        if isinstance(item, dict) and str(item.get("memory_id") or "").strip():
            ids.append(str(item.get("memory_id")))
    return list(dict.fromkeys(ids))


_DATAFLOW_FAILURE_CODES = {
    "ambiguous_streaming_prior_binding",
    "generated_payload_retry_failed",
    "generated_text_prior_reuse_required",
    "llm_text_prior_binding_empty",
    "required_binding_resolved_null",
    "required_binding_unresolved",
    "self_input_binding",
    "streaming_action_id_collision",
    "unknown_action_dependency",
    "unknown_input_binding_source",
}


def _run_has_dataflow_failure(trace: AgentRequestTrace, metadata: dict[str, Any]) -> bool:
    errors = _validation_errors_from_metadata(metadata)
    for error in errors:
        if str(error.get("error") or "") in _DATAFLOW_FAILURE_CODES:
            return True
    haystack = " ".join(
        [
            str(trace.error or ""),
            str(metadata.get("error") or ""),
            str(metadata.get("operator_error") or ""),
        ]
    ).lower()
    return any(
        token in haystack
        for token in (
            "ambiguous_streaming_prior_binding",
            "required_binding_resolved_null",
            "resolved to null",
            "empty prior binding",
            "source_action_id",
        )
    )


def _cache_event_direct_failure(event: str, detail: dict[str, Any]) -> bool:
    event_text = str(event or "").lower()
    if any(token in event_text for token in ("rejected", "failed", "error")):
        return True
    if str(detail.get("error") or "").strip():
        return True
    for item in list(detail.get("errors") or []):
        if isinstance(item, dict) and str(item.get("error") or "").strip():
            return True
    return False


def _cache_events(trace: AgentRequestTrace, metadata: dict[str, Any], outcome: str) -> list[LearningCacheEventWrite]:
    writes: list[LearningCacheEventWrite] = []
    seen: set[tuple[str, str, str]] = set()
    dataflow_failure = _run_has_dataflow_failure(trace, metadata)

    def add(cache_type: str, cache_id: str, event: str, detail: dict[str, Any], score: Any = None) -> None:
        normalized_id = str(cache_id or "").strip()
        if not normalized_id:
            return
        key = (cache_type, normalized_id, event)
        if key in seen:
            return
        seen.add(key)
        failure_outcome = outcome in {"failure", "validation_failed", "gateway_failed", "user_corrected"}
        direct_failure = _cache_event_direct_failure(event, detail)
        status = (
            "suspect"
            if failure_outcome and (direct_failure or not dataflow_failure)
            else "neutral"
        )
        reason = f"Run outcome: {outcome}"
        if failure_outcome and dataflow_failure and not direct_failure:
            reason = "Run outcome was a downstream dataflow failure; cache event was not directly responsible."
        writes.append(
            LearningCacheEventWrite(
                request_id=trace.request_id,
                cache_type=cache_type,  # type: ignore[arg-type]
                cache_id=normalized_id,
                event=event,
                status=status,  # type: ignore[arg-type]
                outcome=outcome,
                score=_float_or_none(score),
                reason=reason,
                evidence={"event_detail": detail},
            )
        )

    applied = metadata.get("operator_plan_cache_applied")
    if isinstance(applied, dict):
        add("plan", str(applied.get("cache_id") or ""), "applied", applied, applied.get("score"))
    comp = metadata.get("operator_computation_cache")
    if isinstance(comp, dict):
        cache_id = str(comp.get("cache_id") or comp.get("computation_cache_id") or "")
        add("computation", cache_id, "metadata", comp, comp.get("score"))
    command_template = metadata.get("operator_command_template_cache")
    if isinstance(command_template, dict):
        cache_id = str(
            command_template.get("template_id")
            or command_template.get("command_template_id")
            or command_template.get("selected_template_id")
            or ""
        )
        add("command_template", cache_id, "metadata", command_template, command_template.get("score"))
    for event in trace.events:
        detail = _event_detail(event)
        cache_id = str(
            detail.get("cache_id")
            or detail.get("computation_cache_id")
            or detail.get("template_id")
            or detail.get("command_template_id")
            or detail.get("selected_template_id")
            or ""
        )
        if not cache_id:
            continue
        event_type = str(getattr(event, "event_type", "") or "")
        if "command_template" in event_type or cache_id.startswith("cmdtpl"):
            cache_type = "command_template"
        elif "computation" in event_type or cache_id.startswith("comp"):
            cache_type = "computation"
        else:
            cache_type = "plan"
        add(cache_type, cache_id, event_type, detail, detail.get("score") or detail.get("cache_score"))
    return writes


def _float_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _record_command(record: dict[str, Any]) -> str:
    for key in ("command", "shell_command", "code"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    metadata = record.get("metadata")
    if isinstance(metadata, dict):
        for key in ("command", "shell_command", "code"):
            value = str(metadata.get(key) or "").strip()
            if value:
                return value
    return ""


def _action_writes(trace: AgentRequestTrace, records: list[dict[str, Any]]) -> list[LearningActionWrite]:
    writes: list[LearningActionWrite] = []
    for record in records:
        error_preview = safe_text(
            record.get("error")
            or record.get("stderr")
            or record.get("stdout")
            or record.get("output")
            or "",
            limit=1200,
        )
        command = _record_command(record)
        writes.append(
            LearningActionWrite(
                request_id=trace.request_id,
                action_id=str(record.get("action_id") or ""),
                task_id=str(record.get("task_id") or ""),
                kind=str(record.get("kind") or ""),
                title=safe_text(record.get("reason") or record.get("title") or record.get("action_id") or "", limit=500),
                status=str(record.get("status") or ""),
                cwd=str(record.get("cwd") or ""),
                command_hash=hash_text(command),
                exit_code=_int_or_none(record.get("exit_code")),
                error_type=str(record.get("error_type") or ""),
                error_preview=error_preview,
                evidence={
                    "has_command": bool(command),
                    "stdout_preview": safe_text(record.get("stdout") or "", limit=500),
                    "stderr_preview": safe_text(record.get("stderr") or "", limit=500),
                },
            )
        )
    return writes


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def classify_outcome(trace: AgentRequestTrace, records: list[dict[str, Any]]) -> LearningOutcome:
    """Classify one run outcome for learning policy."""

    event_types = {str(getattr(event, "event_type", "") or "") for event in trace.events}
    if "confirmation.denied" in event_types:
        return "denied"
    if trace.confirmation_required:
        return "awaiting_confirmation"
    if trace.clarification_required:
        return "awaiting_clarification"
    if trace.status == "cancelled":
        return "cancelled"
    if trace.status == "failed":
        error_text = f"{trace.error or ''} ".lower()
        if "gateway" in error_text:
            return "gateway_failed"
        if "validation" in error_text:
            return "validation_failed"
        return "failure"
    if records:
        failed_indexes = [
            index
            for index, record in enumerate(records)
            if str(record.get("status") or "").lower() == "error"
        ]
        success_indexes = [
            index
            for index, record in enumerate(records)
            if str(record.get("status") or "").lower() == "success"
        ]
        if failed_indexes and success_indexes and max(success_indexes) > min(failed_indexes):
            return "failed_then_corrected"
        if failed_indexes:
            error_text = " ".join(
                str(record.get(key) or "")
                for record in records
                for key in ("error", "stderr", "stdout")
            ).lower()
            if "gateway" in error_text or "node mismatch" in error_text:
                return "gateway_failed"
            if "validation" in error_text:
                return "validation_failed"
            return "failure"
    if trace.status == "completed":
        return "success"
    return "no_learning_needed"


def _failed_then_corrected_lesson(
    *,
    trace: AgentRequestTrace,
    metadata: dict[str, Any],
    records: list[dict[str, Any]],
    request_context: dict[str, Any],
) -> LearningLessonWrite | None:
    failed: list[dict[str, Any]] = []
    for record in records:
        if str(record.get("status") or "").lower() == "error":
            failed.append(record)
            continue
        if str(record.get("status") or "").lower() != "success" or not failed:
            continue
        successful_command = _record_command(record)
        failed_commands = [_record_command(item) for item in failed if _record_command(item)]
        if not successful_command or not failed_commands:
            return None
        title = "Remember corrected command pattern"
        instruction = (
            "For similar future requests, avoid the failed command pattern and prefer the corrected "
            "command pattern when the same preconditions apply."
        )
        task_type = str(metadata.get("task_type") or request_context.get("task_type") or "")
        tool_type = str(record.get("kind") or request_context.get("tool_type") or "shell").strip()
        scope = {
            "task_type": task_type,
            "tool_type": tool_type,
            "intent_type": str(metadata.get("intent_type") or request_context.get("intent_type") or ""),
            "gateway_platform": str(request_context.get("gateway_platform") or ""),
            "cwd": str(request_context.get("terminal_cwd") or request_context.get("cwd") or ""),
            "model_name": str(request_context.get("llm_model") or ""),
            "model_family": normalize_model_family(str(request_context.get("llm_model") or "")),
            "memory_scope": "global",
        }
        evidence = {
            "safe_examples": [safe_text(successful_command, limit=1000)],
            "blocked_examples": [safe_text(command, limit=1000) for command in failed_commands],
            "request_id": trace.request_id,
            "failed_action_ids": [str(item.get("action_id") or "") for item in failed],
            "successful_action_id": str(record.get("action_id") or ""),
        }
        dedupe = hash_text(
            "failed_then_corrected|" + "|".join(failed_commands) + "|" + successful_command
        )
        return LearningLessonWrite(
            lesson_type="command_correction",
            title=title,
            summary="The agent recovered from a failed command by using a corrected command pattern.",
            instruction=instruction,
            scope=scope,
            evidence=evidence,
            confidence=0.72,
            source_request_id=trace.request_id,
            tags=["learning-ledger", "command-correction", "shell"],
            rationale="A command failed and a later command in the same run completed successfully.",
            dedupe_key=dedupe,
        )
    return None


def _proposal_scope(
    *,
    metadata: dict[str, Any],
    request_context: dict[str, Any],
    task_type: str = "",
    tool_type: str = "",
    intent_type: str = "",
    validator_error_type: str = "",
) -> dict[str, Any]:
    model_name = str(request_context.get("llm_model") or metadata.get("model_name") or "")
    return {
        "task_type": task_type or str(metadata.get("task_type") or request_context.get("task_type") or ""),
        "tool_type": tool_type or str(metadata.get("tool_type") or request_context.get("tool_type") or ""),
        "intent_type": intent_type or str(metadata.get("intent_type") or request_context.get("intent_type") or ""),
        "validator_error_type": validator_error_type,
        "gateway_platform": str(request_context.get("gateway_platform") or ""),
        "cwd": str(request_context.get("terminal_cwd") or request_context.get("cwd") or ""),
        "model_name": model_name,
        "model_family": normalize_model_family(model_name),
        "memory_scope": "global",
    }


def _create_placeholder_validation_policy_proposal(
    *,
    store: AgentLearningLedgerStore,
    trace: AgentRequestTrace,
    metadata: dict[str, Any],
    request_context: dict[str, Any],
    error: dict[str, Any],
) -> Any | None:
    placeholders = [str(item) for item in list(error.get("placeholders") or []) if str(item).strip()]
    placeholder_text = " ".join(placeholders)
    prompt_text = f"{trace.prompt}\n{placeholder_text}".lower()
    cpp_literal = any(token in prompt_text for token in ("<iostream>", "#include", ".cpp", "c++", "cpp"))
    task_type = "cpp" if cpp_literal else str(metadata.get("task_type") or "")
    intent_type = str(metadata.get("intent_type") or "")
    scope = _proposal_scope(
        metadata=metadata,
        request_context=request_context,
        task_type=task_type,
        tool_type="shell_command",
        intent_type=intent_type or "create",
        validator_error_type="unresolved_shell_placeholder",
    )
    safe_examples = (
        ["cat > main.cpp <<'CPP'\n#include <iostream>\nCPP"]
        if cpp_literal
        else ["value=$(some_lookup_command); printf '%s\\n' \"$value\""]
    )
    blocked_examples = ["echo '<calculated_value>'", "git checkout <branch_name>"]
    instruction = (
        "When an unresolved shell placeholder warning is caused by literal file/source payload, "
        "preserve the literal text with a quoted heredoc, stdin payload, or Python/input binding. "
        "Do not allow real runtime placeholders: replace them with concrete lookup values inside "
        "the same command, or use python_action/python_transform with input bindings."
    )
    title = "Handle literal shell placeholder evidence"
    dedupe = hash_text(
        "validation_policy|unresolved_shell_placeholder|"
        + "|".join(placeholders)
        + "|"
        + (task_type or "generic")
    )
    insight = store.record_insight(
        CapabilityInsightWrite(
            insight_type="validator_failure",
            source_request_id=trace.request_id,
            source_stage="operator_validation",
            source_error_type="unresolved_shell_placeholder",
            target_kind="validation_policy",
            target_id="validation_policy:unresolved_shell_placeholder",
            model_name=scope["model_name"],
            model_family=scope["model_family"],
            gateway_id=str(request_context.get("gateway_id") or ""),
            cwd=scope["cwd"],
            summary=str(error.get("message") or title),
            evidence={"validation_error": error, "placeholders": placeholders},
            dedupe_key="insight|" + dedupe,
        )
    )
    return store.create_proposal(
        CapabilityProposalWrite(
            target_kind="validation_policy",
            title=title,
            summary="Teach the validator/planner how to distinguish literal payload syntax from real shell placeholders.",
            rationale="A deterministic validator error supplied structured unresolved placeholder evidence.",
            confidence=0.82 if cpp_literal else 0.74,
            source="deterministic",
            source_request_id=trace.request_id,
            source_insight_id=insight.insight_id,
            target_id="unresolved_shell_placeholder",
            draft={
                "instruction": instruction,
                "summary": "Literal payloads may contain placeholder-looking syntax.",
                "scope": scope,
                "memory_kind": "validation_policy",
                "validator_error_type": "unresolved_shell_placeholder",
                "tags": ["learning-ledger", "capability-evolution", "validation-policy", "placeholder"],
            },
            evidence={
                "safe_examples": safe_examples,
                "blocked_examples": blocked_examples,
                "validation_error": error,
                "placeholders": placeholders,
            },
            safety_decision={"safe": True, "reason": "Does not weaken hard placeholder validation."},
            dedupe_key=dedupe,
        )
    )


def _create_failed_then_corrected_proposal(
    *,
    store: AgentLearningLedgerStore,
    trace: AgentRequestTrace,
    metadata: dict[str, Any],
    records: list[dict[str, Any]],
    request_context: dict[str, Any],
) -> Any | None:
    failed: list[dict[str, Any]] = []
    for record in records:
        if str(record.get("status") or "").lower() == "error":
            failed.append(record)
            continue
        if str(record.get("status") or "").lower() != "success" or not failed:
            continue
        successful_command = _record_command(record)
        failed_commands = [_record_command(item) for item in failed if _record_command(item)]
        if not successful_command or not failed_commands:
            return None
        scope = _proposal_scope(
            metadata=metadata,
            request_context=request_context,
            tool_type=str(record.get("kind") or "shell_command"),
        )
        dedupe = hash_text(
            "task_memory|failed_then_corrected|" + "|".join(failed_commands) + "|" + successful_command
        )
        insight = store.record_insight(
            CapabilityInsightWrite(
                insight_type="corrected_action",
                source_request_id=trace.request_id,
                source_stage="operator_execution",
                source_error_type=str(failed[-1].get("error_type") or "command_failed"),
                target_kind="task_memory",
                target_id="operator.command_correction",
                model_name=scope["model_name"],
                model_family=scope["model_family"],
                gateway_id=str(request_context.get("gateway_id") or ""),
                cwd=scope["cwd"],
                summary="A failed command was corrected later in the same run.",
                evidence={
                    "failed_commands": [safe_text(command, limit=1000) for command in failed_commands],
                    "successful_command": safe_text(successful_command, limit=1000),
                },
                dedupe_key="insight|" + dedupe,
            )
        )
        return store.create_proposal(
            CapabilityProposalWrite(
                target_kind="task_memory",
                title="Remember corrected command pattern",
                summary="Prefer the corrected command pattern when the same preconditions apply.",
                rationale="A command failed and a later command in the same run succeeded.",
                confidence=0.76,
                source="deterministic",
                source_request_id=trace.request_id,
                source_insight_id=insight.insight_id,
                target_id="operator.command_correction",
                draft={
                    "instruction": (
                        "For similar future requests, avoid the failed command pattern and prefer "
                        "the corrected command pattern when the same preconditions apply."
                    ),
                    "summary": "The agent recovered from a failed command by using a corrected command pattern.",
                    "scope": scope,
                    "memory_kind": "task_memory",
                    "tags": ["learning-ledger", "capability-evolution", "command-correction", "shell"],
                },
                evidence={
                    "safe_examples": [safe_text(successful_command, limit=1000)],
                    "blocked_examples": [safe_text(command, limit=1000) for command in failed_commands],
                    "failed_action_ids": [str(item.get("action_id") or "") for item in failed],
                    "successful_action_id": str(record.get("action_id") or ""),
                },
                safety_decision={"safe": True, "reason": "Correction memory does not bypass approval."},
                dedupe_key=dedupe,
            )
        )
    return None


def _create_capability_gap_proposal(
    *,
    store: AgentLearningLedgerStore,
    trace: AgentRequestTrace,
    metadata: dict[str, Any],
    request_context: dict[str, Any],
    gap: dict[str, Any],
) -> Any | None:
    task_id = str(gap.get("task_id") or "")
    message = safe_text(gap.get("message") or gap.get("user_facing_message") or "", limit=1000)
    suggested_domain = safe_text(gap.get("suggested_domain") or "", limit=100)
    suggested_object_type = safe_text(gap.get("suggested_object_type") or "", limit=100)
    target_kind = (
        "executable_backend_patch"
        if isinstance(gap.get("backend_patch") or gap.get("proposed_patch"), (dict, str, list))
        else "capability_manifest_overlay"
    )
    target_id = str(gap.get("capability_id") or "operator.shell_command")
    dedupe = hash_text(f"{target_kind}|gap|{target_id}|{suggested_domain}|{suggested_object_type}|{message}")
    scope = _proposal_scope(metadata=metadata, request_context=request_context)
    insight = store.record_insight(
        CapabilityInsightWrite(
            insight_type="capability_gap",
            source_request_id=trace.request_id,
            source_stage="capability_fit",
            source_error_type="capability_gap",
            target_kind=target_kind,  # type: ignore[arg-type]
            target_id=target_id,
            capability_id=target_id,
            model_name=scope["model_name"],
            model_family=scope["model_family"],
            gateway_id=str(request_context.get("gateway_id") or ""),
            cwd=scope["cwd"],
            summary=message or "Capability gap detected.",
            evidence={"gap": gap},
            dedupe_key="insight|" + dedupe,
        )
    )
    if target_kind == "executable_backend_patch":
        draft = {
            "capability_id": target_id,
            "patch": gap.get("backend_patch") or gap.get("proposed_patch"),
            "notes": message,
        }
    else:
        draft = {
            "capability_id": target_id,
            "overlay": {
                "semantic_tags": [item for item in [suggested_domain, suggested_object_type] if item],
                "object_types": [suggested_object_type] if suggested_object_type else [],
                "examples": [{"prompt": safe_text(trace.prompt, limit=240)}],
                "safety_notes": [message] if message else [],
            },
        }
    return store.create_proposal(
        CapabilityProposalWrite(
            target_kind=target_kind,  # type: ignore[arg-type]
            title="Record capability gap guidance",
            summary=message or "Planner-visible guidance for a repeated capability gap.",
            rationale="Capability fit reported a gap that may be avoided with planner guidance or a backend patch.",
            confidence=0.62 if target_kind == "executable_backend_patch" else 0.73,
            source="deterministic",
            source_request_id=trace.request_id,
            source_insight_id=insight.insight_id,
            target_id=target_id,
            draft=draft,
            evidence={"gap": gap, "scope": scope},
            safety_decision={
                "safe": target_kind != "executable_backend_patch",
                "reason": (
                    "Manifest overlay can only add planner-visible guidance."
                    if target_kind != "executable_backend_patch"
                    else "Executable backend patches require explicit code application."
                ),
            },
            dedupe_key=dedupe,
        )
    )


def feedback_lesson_write(
    *,
    request_id: str,
    feedback: str,
    prompt: str = "",
    outcome: str = "",
    model_name: str = "",
    feedback_target: str = "",
) -> LearningLessonWrite | None:
    """Create a draft lesson from explicit user feedback."""

    text = safe_text(feedback, limit=1600)
    if not text:
        return None
    lesson_type = "validation_policy" if "validation" in feedback_target.lower() else "general"
    if outcome == "wrong_decision":
        lesson_type = "negative_pattern"
    title = "Remember user correction"
    instruction = f"For future similar requests, follow this user correction: {text}"
    return LearningLessonWrite(
        lesson_type=lesson_type,  # type: ignore[arg-type]
        title=title,
        summary="User supplied post-run feedback that may improve future runs.",
        instruction=instruction,
        scope={
            "model_name": model_name,
            "model_family": normalize_model_family(model_name),
            "memory_scope": "global",
        },
        evidence={
            "request_id": request_id,
            "prompt": safe_text(prompt, limit=500),
            "feedback": text,
            "outcome": outcome,
        },
        confidence=0.82,
        source_request_id=request_id,
        tags=["learning-ledger", "user-feedback", outcome or "feedback"],
        rationale="User feedback is stronger evidence than automatic inference.",
        dedupe_key=hash_text(f"user-feedback|{request_id}|{text}"),
    )


def analyze_and_record_run(
    *,
    store: AgentLearningLedgerStore,
    trace: AgentRequestTrace,
    request_context: dict[str, Any] | None = None,
    planning_trace: Any = None,
) -> list[Any]:
    """Persist one run's evidence and return newly relevant draft lessons."""

    context = dict(request_context or {})
    metadata = _metadata(planning_trace)
    records = _records_from_metadata(metadata)
    outcome = classify_outcome(trace, records)
    metrics = trace.response_metrics or {}
    gateway_routing = context.get("gateway_routing") if isinstance(context.get("gateway_routing"), dict) else {}
    gateway_routing = dict(gateway_routing or {})
    cache_events = _cache_events(trace, metadata, outcome)
    cache_ids = list(dict.fromkeys([event.cache_id for event in cache_events if event.cache_id]))
    run = LearningRunWrite(
        request_id=trace.request_id,
        conversation_id=str(context.get("conversation_id") or metadata.get("conversation_id") or ""),
        parent_request_id=str(context.get("parent_request_id") or metadata.get("parent_request_id") or ""),
        agent_mode=str(context.get("agent_mode") or metadata.get("agent_mode") or ""),
        prompt=trace.prompt,
        prompt_signature=prompt_signature(trace.prompt),
        prompt_excerpt=safe_text(trace.prompt, limit=500),
        model_name=str(context.get("llm_model") or metadata.get("model_name") or ""),
        model_family=normalize_model_family(str(context.get("llm_model") or metadata.get("model_name") or "")),
        gateway_id=str(context.get("gateway_id") or gateway_routing.get("gateway_id") or ""),
        gateway_nickname=str(gateway_routing.get("gateway_nickname") or context.get("gateway_label") or ""),
        gateway_node=str(context.get("gateway_node") or gateway_routing.get("gateway_node") or ""),
        gateway_platform=str(
            context.get("gateway_platform")
            or gateway_routing.get("gateway_platform")
            or gateway_routing.get("platform")
            or ""
        ),
        cwd=str(context.get("terminal_cwd") or context.get("cwd") or ""),
        workspace_root=str(context.get("workspace_root") or ""),
        status=trace.status,
        outcome=outcome,
        duration_ms=_float_or_none(metrics.get("duration_ms")),
        input_tokens_estimate=_int_or_zero(metrics.get("input_tokens_estimate")),
        output_tokens_estimate=_int_or_zero(metrics.get("output_tokens_estimate")),
        total_tokens_estimate=_int_or_zero(metrics.get("total_tokens_estimate")),
        llm_call_count=_int_or_zero(metrics.get("llm_call_count")),
        confirmation_required=trace.confirmation_required,
        clarification_required=trace.clarification_required,
        auto_approved=any(
            str(getattr(event, "event_type", "") or "") in {"confirmation.auto_approved", "scheduled_event.auto_approved"}
            for event in trace.events
        ),
        memory_ids=_memory_ids(metadata),
        cache_ids=cache_ids,
        error=safe_text(trace.error or "", limit=1000),
        final_response_preview=safe_text(trace.final_response or "", limit=1000),
        evidence={
            "record_count": len(records),
            "event_count": len(trace.events),
            "confirmation_required": trace.confirmation_required,
            "clarification_required": trace.clarification_required,
        },
    )
    store.record_run(run)
    for action in _action_writes(trace, records):
        store.record_action(action)
    for cache_event in cache_events:
        store.record_cache_event(cache_event)
    lessons = []
    if outcome == "failed_then_corrected":
        lesson_write = _failed_then_corrected_lesson(
            trace=trace,
            metadata=metadata,
            records=records,
            request_context=context,
        )
        if lesson_write is not None:
            lesson = store.create_lesson(lesson_write)
            if lesson is not None and lesson.status == "draft":
                lessons.append(lesson)
        _create_failed_then_corrected_proposal(
            store=store,
            trace=trace,
            metadata=metadata,
            records=records,
            request_context=context,
        )
    for error in _validation_errors_from_metadata(metadata):
        if str(error.get("error") or "") == "unresolved_shell_placeholder":
            _create_placeholder_validation_policy_proposal(
                store=store,
                trace=trace,
                metadata=metadata,
                request_context=context,
                error=error,
            )
    for gap in _capability_gaps_from_trace(metadata, planning_trace):
        _create_capability_gap_proposal(
            store=store,
            trace=trace,
            metadata=metadata,
            request_context=context,
            gap=gap,
        )
    for raw in list(metadata.get("capability_evolution_llm_proposals") or []):
        if not isinstance(raw, dict):
            continue
        target_kind = str(raw.get("target_kind") or "").strip()
        if target_kind not in {
            "task_memory",
            "validation_policy",
            "prompt_patch",
            "capability_manifest_overlay",
            "executable_backend_patch",
        }:
            continue
        store.create_proposal(
            CapabilityProposalWrite(
                target_kind=target_kind,  # type: ignore[arg-type]
                title=safe_text(raw.get("title") or "Capability evolution proposal", limit=240),
                summary=safe_text(raw.get("summary") or "", limit=1000),
                rationale=safe_text(raw.get("rationale") or "LLM-drafted proposal from run evidence.", limit=2000),
                confidence=_float_or_none(raw.get("confidence")) or 0.5,
                source="llm",
                source_request_id=trace.request_id,
                target_id=str(raw.get("target_id") or ""),
                draft=dict(raw.get("draft") or {}),
                evidence=dict(raw.get("evidence") or {}),
                safety_decision=dict(raw.get("safety_decision") or {}),
                dedupe_key=hash_text(
                    "llm|"
                    + target_kind
                    + "|"
                    + str(raw.get("target_id") or "")
                    + "|"
                    + safe_text(raw.get("title") or "", limit=240)
                ),
            )
        )
    return lessons


def _int_or_zero(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None
