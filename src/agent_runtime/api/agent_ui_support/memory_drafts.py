"""Memory draft normalization, optimizer prompts, and SSE serialization helpers."""

from __future__ import annotations

from agent_runtime.api.agent_ui_support.common import *
from agent_runtime.api.agent_ui_support.directory import *
from agent_runtime.api.agent_ui_support.memory_utils import *
from agent_runtime.api.agent_ui_support.models import *
from agent_runtime.api.agent_ui_support.prompt_context import *
from agent_runtime.api.agent_ui_support.memory import *
from agent_runtime.api.agent_ui_support.events import *

def _memory_tags(values: Any) -> list[str]:
    """Normalize memory tags while preserving user/LLM intent."""

    if not isinstance(values, list):
        return []
    seen: set[str] = set()
    tags: list[str] = []
    for value in values:
        tag = _memory_slug(value)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
    return tags[:20]


def _memory_kind(value: Any, *, fallback: str = "task_memory") -> str:
    """Return one supported memory kind."""

    raw = _memory_slug(value)
    if raw in {"task_memory", "preference_memory", "validation_policy"}:
        return raw
    return fallback


def _memory_examples(values: Any) -> list[str]:
    """Normalize example lists for validation-policy memory."""

    if not isinstance(values, list):
        return []
    examples: list[str] = []
    for value in values:
        text = " ".join(str(value or "").split()).strip()
        if text:
            examples.append(text[:300])
        if len(examples) >= 8:
            break
    return examples


def _memory_feedback_text(payload: MemoryFeedbackRequest) -> str:
    """Return compact lower-case feedback context for routing heuristics."""

    pieces = [payload.feedback, payload.prompt, payload.validator_error_type]
    if payload.run_feedback is not None:
        pieces.extend(
            [
                payload.run_feedback.prompt,
                payload.run_feedback.final_response,
                payload.run_feedback.error,
                payload.run_feedback.outcome,
            ]
        )
    return " ".join(str(piece or "") for piece in pieces).lower()


def _feedback_seems_validator_policy(payload: MemoryFeedbackRequest) -> bool:
    """Return whether validation-targeted feedback is truly about validator behavior."""

    if payload.feedback_target != "validation_policy":
        return False
    if _memory_slug(payload.validator_error_type):
        return True
    text = _memory_feedback_text(payload)
    validator_markers = (
        "validator",
        "validation error",
        "validation failure",
        "deterministic validation",
        "unresolved_shell_placeholder",
        "placeholder false positive",
        "literal payload",
        "blocked syntax",
        "adjudication",
        "#include",
        "<iostream>",
        "<calculated_value>",
        "{{",
        "}}",
    )
    return any(marker in text for marker in validator_markers)


def _memory_kind_fallback_for_feedback(payload: MemoryFeedbackRequest) -> str:
    """Choose the safest memory-kind fallback for user feedback."""

    if _feedback_seems_validator_policy(payload):
        return "validation_policy"
    return "task_memory"


def _inferred_feedback_memory_shape(payload: MemoryFeedbackRequest) -> dict[str, str]:
    """Infer targeted retrieval fields when the LLM did not provide them."""

    text = _memory_feedback_text(payload)
    task_type = _memory_slug(payload.task_type)
    tool_type = _memory_slug(payload.tool_type)
    intent_type = _memory_slug(payload.intent_type)
    if not task_type:
        if "docker" in text or "container" in text:
            task_type = "docker"
        elif "git" in text:
            task_type = "git"
    if not tool_type:
        if any(token in text for token in ("docker", "git", "shell", "terminal", "command")):
            tool_type = "shell"
    if not intent_type:
        verification_markers = (
            "verify",
            "verifying",
            "validation",
            "validating",
            "check",
            "checking",
            "postcondition",
            "empty output",
            "no output",
            "produce any output",
        )
        if any(marker in text for marker in verification_markers):
            intent_type = "verify_state"
        elif "plan" in text or "strategy" in text:
            intent_type = "planning"
    return {
        "task_type": task_type,
        "tool_type": tool_type,
        "intent_type": intent_type,
    }


def _inferred_feedback_examples(payload: MemoryFeedbackRequest) -> tuple[list[str], list[str]]:
    """Infer small safe/blocked examples for common workflow feedback."""

    text = _memory_feedback_text(payload)
    if (
        ("docker" in text or "container" in text)
        and "ps" in text
        and any(marker in text for marker in ("empty output", "no output", "not produce any output"))
    ):
        return (
            [
                "docker ps may produce no stdout when no containers are running; treat empty output as valid evidence for an empty list.",
            ],
            [
                "Do not fail or retry merely because docker ps stdout is empty.",
                "Do not validate by requiring docker ps to print at least one container row.",
            ],
        )
    return [], []


def _normalized_memory_draft_payload(
    draft: Any,
    payload: MemoryFeedbackRequest,
    settings: Settings,
) -> dict[str, Any]:
    """Normalize one LLM memory proposal into the DB-facing draft shape."""

    draft_payload = draft.model_dump(
        mode="json",
        exclude={"proposal_type", "memory_id", "confidence"},
    )
    model_name = str(
        draft_payload.get("model_name")
        or payload.model_name
        or _active_agent_model_for_memory(settings)
        or ""
    ).strip()
    model_family = str(draft_payload.get("model_family") or normalize_model_family(model_name)).strip()
    fallback_kind = _memory_kind_fallback_for_feedback(payload)
    memory_kind = _memory_kind(draft_payload.get("memory_kind"), fallback=fallback_kind)
    inferred = _inferred_feedback_memory_shape(payload)
    task_type = _memory_slug(draft_payload.get("task_type") or payload.task_type) or inferred["task_type"]
    tool_type = _memory_slug(draft_payload.get("tool_type") or payload.tool_type) or inferred["tool_type"]
    intent_type = _memory_slug(draft_payload.get("intent_type") or payload.intent_type) or inferred["intent_type"]
    validator_error_type = _memory_slug(
        draft_payload.get("validator_error_type") or payload.validator_error_type
    )
    if memory_kind == "validation_policy" and not validator_error_type and not _feedback_seems_validator_policy(payload):
        memory_kind = "task_memory"
    tags = _memory_tags(draft_payload.get("tags"))
    if memory_kind == "validation_policy" and memory_kind not in tags:
        tags.append(memory_kind)
    if validator_error_type and validator_error_type not in tags:
        tags.append(validator_error_type)
    for value in (task_type, tool_type, intent_type):
        if value and value not in tags:
            tags.append(value)
    inferred_safe_examples, inferred_blocked_examples = _inferred_feedback_examples(payload)
    safe_examples = _memory_examples(draft_payload.get("safe_examples")) or inferred_safe_examples
    blocked_examples = _memory_examples(draft_payload.get("blocked_examples")) or inferred_blocked_examples
    draft_payload.update(
        {
            "instruction": str(draft_payload.get("instruction") or "").strip(),
            "summary": str(draft_payload.get("summary") or "").strip(),
            "memory_kind": memory_kind,
            "model_name": model_name,
            "model_family": model_family,
            "task_type": task_type,
            "tool_type": tool_type,
            "intent_type": intent_type,
            "validator_error_type": validator_error_type,
            "safe_examples": safe_examples,
            "blocked_examples": blocked_examples,
            "tags": tags[:20],
            "rationale": str(draft_payload.get("rationale") or "").strip(),
        }
    )
    return draft_payload


def _fallback_memory_feedback_draft_payload(
    payload: MemoryFeedbackRequest,
    settings: Settings,
) -> dict[str, Any]:
    """Convert explicit user feedback into one conservative proposal without LLM help."""

    model_name = str(payload.model_name or _active_agent_model_for_memory(settings) or "").strip()
    inferred = _inferred_feedback_memory_shape(payload)
    task_type = _memory_slug(payload.task_type) or inferred["task_type"]
    tool_type = _memory_slug(payload.tool_type) or inferred["tool_type"]
    intent_type = _memory_slug(payload.intent_type) or inferred["intent_type"]
    validator_error_type = _memory_slug(payload.validator_error_type)
    memory_kind = _memory_kind_fallback_for_feedback(payload)
    tags = _memory_tags([memory_kind, task_type, tool_type, intent_type, validator_error_type])
    instruction = " ".join(str(payload.feedback or "").split()).strip()
    summary = instruction[:140]
    safe_examples, blocked_examples = _inferred_feedback_examples(payload)
    return {
        "instruction": instruction,
        "summary": summary,
        "memory_kind": memory_kind,
        "scope": "global",
        "model_name": model_name,
        "model_family": normalize_model_family(model_name),
        "task_type": task_type,
        "tool_type": tool_type,
        "intent_type": intent_type,
        "validator_error_type": validator_error_type,
        "safe_examples": safe_examples,
        "blocked_examples": blocked_examples,
        "tags": tags[:20],
        "rationale": "Fallback proposal created directly from user feedback because LLM feedback drafting was unavailable or empty.",
    }


def _memory_context_draft_prompt(payload: MemoryContextDraftRequest, settings: Settings, trace: Any) -> str:
    """Build a prompt that extracts memory association metadata from request context."""

    model_name = str(payload.model_name or _active_agent_model_for_memory(settings)).strip()
    trace_payload = {}
    if trace is not None:
        trace_payload = {
            "request_id": trace.request_id,
            "prompt": trace.prompt,
            "status": trace.status,
            "final_response": trace.final_response,
            "error": trace.error,
        }
    return "\n".join(
        [
            *prompt_lines("memory.context_draft"),
            "MemoryContextDraftResponse schema:",
            json.dumps(MemoryContextDraftResponse.model_json_schema(), default=str),
            "Current model:",
            model_name,
            "Model family:",
            normalize_model_family(model_name),
            "Request text:",
            payload.prompt,
            "Response text:",
            payload.final_response[:8000],
            "Trace summary:",
            json.dumps(trace_payload, default=str, ensure_ascii=True),
        ]
    )


def _memory_optimizer_prompt(entries: list[dict[str, Any]], query: str) -> str:
    """Build a prompt for suggested memory cleanup proposals."""

    return "\n".join(
        [
            *prompt_lines("memory.optimizer"),
            "MemoryDraftResponse schema:",
            json.dumps(MemoryDraftResponse.model_json_schema(), default=str),
            "User optimization focus:",
            str(query or ""),
            "Current memory entries:",
            json.dumps(entries, default=str, ensure_ascii=True),
        ]
    )


def format_sse(event_name: str, payload: Any) -> str:
    """Return one named server-sent event frame."""

    return f"event: {event_name}\ndata: {json_dumps(payload)}\n\n"

__all__ = [name for name in globals() if not name.startswith("__")]
