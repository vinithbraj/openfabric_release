"""Scoped runtime helpers for persistent memory retrieval and rendering."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from agent_runtime.core.types import UserRequest
from agent_runtime.memory.models import MemoryRetrievalContext


ACTIVE_SCOPE_KEY = "agent_memory_active_scope"
SCOPED_CACHE_KEY = "agent_memory_scoped_cache"
SCOPED_TRACE_KEY = "agent_memory_scopes"
MAX_SCOPED_MEMORY_CACHE_ENTRIES = 16


def stable_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, indent=2, default=str)
    except Exception:
        return str(value)


def scoped_current_task(context: dict[str, Any]) -> dict[str, Any]:
    current_task = context.get("operator_streaming_current_task")
    return dict(current_task) if isinstance(current_task, dict) else {}


def scoped_current_task_id(context: dict[str, Any]) -> str:
    return str(scoped_current_task(context).get("task_id") or "").strip()


def scoped_memory_enabled(context: dict[str, Any]) -> bool:
    return isinstance(context.get(SCOPED_CACHE_KEY), dict) or isinstance(
        context.get(ACTIVE_SCOPE_KEY),
        dict,
    )


def cached_memory_scope(context: dict[str, Any], scope_id: str) -> dict[str, Any] | None:
    cache = context.get(SCOPED_CACHE_KEY)
    if not isinstance(cache, dict):
        return None
    scope = cache.get(str(scope_id or "").strip())
    return dict(scope) if isinstance(scope, dict) else None


def scoped_memory_scope_id(
    user_request: UserRequest,
    *,
    stage: str,
    source: str,
    retrieval: MemoryRetrievalContext | None,
) -> str:
    context = dict(user_request.session_context or {})
    current_task = scoped_current_task(context)
    payload = {
        "stage": stage,
        "source": source,
        "request_id": str(user_request.request_id or ""),
        "prompt": str(user_request.raw_prompt or ""),
        "streaming_step_id": str(context.get("operator_streaming_step_id") or ""),
        "current_index": context.get("operator_streaming_current_index"),
        "current_task": {
            key: current_task.get(key)
            for key in (
                "task_id",
                "description",
                "goal",
                "semantic_verb",
                "object_type",
                "operation_intent",
            )
            if current_task.get(key) not in (None, "")
        },
        "retrieval": retrieval.model_dump(mode="json") if retrieval is not None else {},
    }
    digest = hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()[:20]
    return f"memscope_{digest}"


def scoped_empty_memory_scope(
    user_request: UserRequest,
    *,
    stage: str,
    source: str,
    retrieval: MemoryRetrievalContext | None = None,
    error: str = "",
    disabled: bool = False,
) -> dict[str, Any]:
    context = user_request.session_context
    current_task = scoped_current_task(context)
    return {
        "scope_id": scoped_memory_scope_id(
            user_request,
            stage=stage,
            source=source,
            retrieval=retrieval,
        ),
        "stage": stage,
        "source": source,
        "task_id": str(current_task.get("task_id") or ""),
        "streaming_step_id": str(context.get("operator_streaming_step_id") or ""),
        "retrieval_context": retrieval.model_dump(mode="json") if retrieval is not None else {},
        "memory": [],
        "matches": [],
        "directives": [],
        "memory_ids": [],
        "retrieved_memory_ids": [],
        "retrieved_candidate_ids": [],
        "memory_use_count": 0,
        "error": error,
        "disabled": disabled,
    }


def scoped_brief_payload(brief: Any) -> dict[str, Any]:
    if brief is None:
        return {}
    contract = getattr(brief, "verification_contract", None)
    return {
        "task_understanding": getattr(brief, "task_understanding", ""),
        "success_postcondition": getattr(brief, "success_postcondition", ""),
        "recommended_strategy": getattr(brief, "recommended_strategy", ""),
        "verification_goal": getattr(contract, "goal", ""),
        "freshness": getattr(contract, "freshness", ""),
        "domain_facts": list(getattr(brief, "key_domain_facts", [])[:5]),
        "pitfalls": list(getattr(brief, "likely_pitfalls", [])[:5]),
    }


def scoped_retrieval_prompt(
    user_request: UserRequest,
    *,
    stage: str,
    brief: Any = None,
) -> str:
    context = dict(user_request.session_context or {})
    current_task = scoped_current_task(context)
    parts: list[str] = [f"memory_stage: {stage}"]
    if current_task:
        parts.extend(["current_task", stable_json(current_task)])
        prior_results = context.get("operator_streaming_prior_results")
        if prior_results:
            parts.extend(["operator_streaming_prior_results", stable_json(prior_results)])
    else:
        parts.append(str(user_request.raw_prompt or ""))
        intent_block = context.get("operator_intent_block")
        if isinstance(intent_block, dict):
            parts.extend(["operator_intent_block", stable_json(intent_block)])
    brief_payload = scoped_brief_payload(brief)
    if brief_payload:
        parts.extend(["self_brief", stable_json(brief_payload)])
    return "\n".join(part for part in parts if str(part or "").strip())[:12000]


def scoped_memory_tags(
    user_request: UserRequest,
    *,
    pre_clarification_tags: list[str] | None = None,
    self_brief_tags: list[str] | None = None,
) -> list[str]:
    context = dict(user_request.session_context or {})
    current_task = scoped_current_task(context)
    pieces: list[str] = []
    if current_task:
        pieces.extend(
            str(current_task.get(key) or "")
            for key in (
                "task_id",
                "description",
                "goal",
                "semantic_verb",
                "object_type",
                "operation_intent",
            )
        )
    elif self_brief_tags is not None:
        pieces.extend(self_brief_tags)
    elif pre_clarification_tags is not None:
        pieces.extend(pre_clarification_tags)
    for key in ("agent_mode", "mode", "operator_mode_label"):
        value = str(context.get(key) or "").strip()
        if value:
            pieces.append(value)
    return [piece for piece in pieces if str(piece or "").strip()]


def set_active_memory_scope(
    context: dict[str, Any],
    *,
    scope: dict[str, Any],
    limit: int = MAX_SCOPED_MEMORY_CACHE_ENTRIES,
) -> None:
    context[ACTIVE_SCOPE_KEY] = dict(scope)
    context["agent_memory_context"] = dict(scope.get("retrieval_context") or {})
    context["agent_memory"] = list(scope.get("memory") or [])
    context["agent_memory_matches"] = list(scope.get("matches") or [])
    context["agent_memory_directives"] = list(scope.get("directives") or [])
    cache = context.setdefault(SCOPED_CACHE_KEY, {})
    if not isinstance(cache, dict):
        return
    scope_id = str(scope.get("scope_id") or "").strip()
    if scope_id:
        cache[scope_id] = dict(scope)
    while len(cache) > limit:
        first_key = next(iter(cache))
        if first_key == scope_id and len(cache) == 1:
            break
        cache.pop(first_key, None)


def active_scope_raw_directives(
    context: dict[str, Any],
    *,
    stage: str | None,
) -> tuple[list[Any] | None, dict[str, Any] | None]:
    """Return raw directives from the active scope when it matches stage/task."""

    active_scope = context.get(ACTIVE_SCOPE_KEY)
    if not isinstance(active_scope, dict):
        return None, None
    requested_stage = str(stage or "").strip().lower()
    active_stage = str(active_scope.get("stage") or "").strip().lower()
    if requested_stage and active_stage and requested_stage != active_stage:
        return [], active_scope
    active_task_id = str(active_scope.get("task_id") or "").strip()
    current_task_id = scoped_current_task_id(context)
    if active_task_id and current_task_id and active_task_id != current_task_id:
        return [], active_scope
    if active_task_id and not current_task_id:
        return [], active_scope
    raw_directives = active_scope.get("directives")
    return list(raw_directives) if isinstance(raw_directives, list) else [], active_scope


def memory_scope_tuple(context: dict[str, Any]) -> tuple[str, str, str]:
    active_scope = context.get(ACTIVE_SCOPE_KEY)
    if isinstance(active_scope, dict):
        return (
            str(active_scope.get("scope_id") or ""),
            str(active_scope.get("stage") or "clarification"),
            str(active_scope.get("task_id") or ""),
        )
    return "", "clarification", scoped_current_task_id(context)


def memory_scope_from_matches(
    user_request: UserRequest,
    *,
    stage: str,
    source: str,
    retrieval: MemoryRetrievalContext,
    entry_payloads: list[dict[str, Any]],
    match_payloads: list[dict[str, Any]],
    directives: list[dict[str, Any]],
    memory_use_counts: dict[str, int],
) -> dict[str, Any]:
    context = user_request.session_context
    current_task = scoped_current_task(context)
    memory_ids = [
        str(entry.get("memory_id") or "")
        for entry in entry_payloads
        if str(entry.get("memory_id") or "").strip()
    ]
    return {
        "scope_id": scoped_memory_scope_id(
            user_request,
            stage=stage,
            source=source,
            retrieval=retrieval,
        ),
        "stage": stage,
        "source": source,
        "task_id": str(current_task.get("task_id") or ""),
        "streaming_step_id": str(context.get("operator_streaming_step_id") or ""),
        "retrieval_context": retrieval.model_dump(mode="json"),
        "memory": list(entry_payloads),
        "matches": list(match_payloads),
        "directives": list(directives),
        "memory_ids": memory_ids,
        "retrieved_memory_ids": memory_ids,
        "retrieved_candidate_ids": memory_ids,
        "memory_use_count": sum(memory_use_counts.values()),
        "memory_use_counts": dict(memory_use_counts),
    }


def scope_observability_details(
    scope: dict[str, Any],
    *,
    memory_count: int | None = None,
    error: str = "",
) -> dict[str, Any]:
    memory_ids = list(scope.get("memory_ids") or scope.get("retrieved_memory_ids") or [])
    directives = list(scope.get("directives") or [])
    return {
        "memory_count": len(memory_ids) if memory_count is None else int(memory_count),
        "memory_ids": memory_ids,
        "retrieved_memory_ids": memory_ids,
        "retrieved_candidate_ids": list(scope.get("retrieved_candidate_ids") or memory_ids),
        "rendered_directive_ids": list(scope.get("rendered_directive_ids") or []),
        "filtered_directive_ids": list(scope.get("filtered_directive_ids") or []),
        "skipped_memory_question_ids": list(scope.get("skipped_memory_question_ids") or []),
        "memory_use_count": int(scope.get("memory_use_count") or 0),
        "memory_use_counts": dict(scope.get("memory_use_counts") or {}),
        "scope_id": str(scope.get("scope_id") or ""),
        "stage": str(scope.get("stage") or ""),
        "task_id": str(scope.get("task_id") or ""),
        "retrieval_context": dict(scope.get("retrieval_context") or {}),
        "matches": list(scope.get("matches") or []),
        "directives": directives,
        "source": str(scope.get("source") or ""),
        "error": error or str(scope.get("error") or ""),
        "active_scope": dict(scope),
    }


def update_memory_scope_trace(
    metadata: dict[str, Any],
    details: dict[str, Any],
    session_context: dict[str, Any],
) -> None:
    """Mirror scoped memory diagnostics into trace metadata."""

    metadata["agent_memory_count"] = int(details.get("memory_count") or 0)
    metadata["agent_memory_use_count"] = int(details.get("memory_use_count") or 0)
    metadata["agent_memory_context"] = dict(details.get("retrieval_context") or {})
    metadata["agent_memory_matches"] = list(details.get("matches") or [])
    metadata["agent_memory_directives"] = list(details.get("directives") or [])
    scoped_cache = session_context.get(SCOPED_CACHE_KEY)
    if isinstance(scoped_cache, dict):
        metadata[SCOPED_CACHE_KEY] = dict(scoped_cache)
    scope_id = str(details.get("scope_id") or "").strip()
    if scope_id:
        scopes = metadata.setdefault(SCOPED_TRACE_KEY, {})
        if isinstance(scopes, dict):
            previous = dict(scopes.get(scope_id) or {})
            previous.update(
                {
                    "scope_id": scope_id,
                    "stage": details.get("stage"),
                    "task_id": details.get("task_id"),
                    "source": details.get("source"),
                    "memory_count": int(details.get("memory_count") or 0),
                    "retrieved_memory_ids": list(details.get("retrieved_memory_ids") or []),
                    "retrieved_candidate_ids": list(details.get("retrieved_candidate_ids") or []),
                    "rendered_directive_ids": list(details.get("rendered_directive_ids") or []),
                    "filtered_directive_ids": list(details.get("filtered_directive_ids") or []),
                    "skipped_memory_question_ids": list(
                        details.get("skipped_memory_question_ids") or []
                    ),
                    "retrieval_context": dict(details.get("retrieval_context") or {}),
                    "error": str(details.get("error") or ""),
                }
            )
            scopes[scope_id] = previous
    if details.get("error"):
        metadata["agent_memory_error"] = str(details.get("error"))


def record_scope_prompt_render(
    user_request: UserRequest,
    *,
    stage: str,
    scope_id: str,
    rendered_ids: list[str],
    filtered_ids: list[str],
) -> None:
    if not scope_id:
        return
    context = user_request.session_context
    active_scope = context.get(ACTIVE_SCOPE_KEY)
    if isinstance(active_scope, dict) and str(active_scope.get("scope_id") or "") == scope_id:
        active_scope["rendered_directive_ids"] = list(rendered_ids)
        active_scope["filtered_directive_ids"] = list(filtered_ids)
        active_scope.setdefault("rendered_directive_ids_by_stage", {})[stage] = list(rendered_ids)
        active_scope.setdefault("filtered_directive_ids_by_stage", {})[stage] = list(filtered_ids)
        context[ACTIVE_SCOPE_KEY] = active_scope
        cache = context.get(SCOPED_CACHE_KEY)
        if isinstance(cache, dict) and isinstance(cache.get(scope_id), dict):
            cache[scope_id].update(
                {
                    "rendered_directive_ids": list(rendered_ids),
                    "filtered_directive_ids": list(filtered_ids),
                    "rendered_directive_ids_by_stage": dict(
                        active_scope.get("rendered_directive_ids_by_stage") or {}
                    ),
                    "filtered_directive_ids_by_stage": dict(
                        active_scope.get("filtered_directive_ids_by_stage") or {}
                    ),
                }
            )
    trace_metadata = getattr(user_request.safety_context.get("planning_trace"), "metadata", None)
    for target in (context, trace_metadata):
        if not isinstance(target, dict):
            continue
        scopes = target.setdefault(SCOPED_TRACE_KEY, {})
        if not isinstance(scopes, dict):
            continue
        scope_payload = dict(scopes.get(scope_id) or {})
        scope_payload.update(
            {
                "scope_id": scope_id,
                "stage": stage,
                "rendered_directive_ids": list(rendered_ids),
                "filtered_directive_ids": list(filtered_ids),
            }
        )
        scope_payload.setdefault("rendered_directive_ids_by_stage", {})[stage] = list(rendered_ids)
        scope_payload.setdefault("filtered_directive_ids_by_stage", {})[stage] = list(filtered_ids)
        scopes[scope_id] = scope_payload


def record_scope_memory_question_skip(
    user_request: UserRequest,
    *,
    scope_id: str,
    stage: str,
    task_id: str,
    memory_id: str,
) -> None:
    if not scope_id or not memory_id:
        return
    context = user_request.session_context
    active_scope = context.get(ACTIVE_SCOPE_KEY)
    if isinstance(active_scope, dict) and str(active_scope.get("scope_id") or "") == scope_id:
        skipped = list(active_scope.get("skipped_memory_question_ids") or [])
        if memory_id not in skipped:
            skipped.append(memory_id)
        active_scope["skipped_memory_question_ids"] = skipped
        context[ACTIVE_SCOPE_KEY] = active_scope
        cache = context.get(SCOPED_CACHE_KEY)
        if isinstance(cache, dict) and isinstance(cache.get(scope_id), dict):
            cache[scope_id]["skipped_memory_question_ids"] = list(skipped)
    trace_metadata = getattr(user_request.safety_context.get("planning_trace"), "metadata", None)
    for target in (context, trace_metadata):
        if not isinstance(target, dict):
            continue
        scopes = target.setdefault(SCOPED_TRACE_KEY, {})
        if not isinstance(scopes, dict):
            continue
        scope_payload = dict(scopes.get(scope_id) or {})
        skipped = list(scope_payload.get("skipped_memory_question_ids") or [])
        if memory_id not in skipped:
            skipped.append(memory_id)
        scope_payload.update(
            {
                "scope_id": scope_id,
                "stage": stage,
                "task_id": task_id,
                "skipped_memory_question_ids": skipped,
            }
        )
        scopes[scope_id] = scope_payload


__all__ = [
    "ACTIVE_SCOPE_KEY",
    "SCOPED_CACHE_KEY",
    "SCOPED_TRACE_KEY",
    "active_scope_raw_directives",
    "cached_memory_scope",
    "memory_scope_from_matches",
    "memory_scope_tuple",
    "record_scope_memory_question_skip",
    "record_scope_prompt_render",
    "scope_observability_details",
    "scoped_brief_payload",
    "scoped_current_task",
    "scoped_current_task_id",
    "scoped_empty_memory_scope",
    "scoped_memory_enabled",
    "scoped_memory_scope_id",
    "scoped_memory_tags",
    "scoped_retrieval_prompt",
    "set_active_memory_scope",
    "stable_json",
    "update_memory_scope_trace",
]
