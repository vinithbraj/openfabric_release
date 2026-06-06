"""Prompt helpers for persistent agent memory."""

from __future__ import annotations

import json
from typing import Any

from agent_runtime.core.types import UserRequest
from agent_runtime.memory.models import MemoryDirective, MemoryEntry, MemoryRetrievalMatch
from agent_runtime.memory.scoping import (
    active_scope_raw_directives,
    record_scope_prompt_render,
    scoped_memory_enabled,
)


_DIRECTIVE_INSTRUCTION_MAX_CHARS = 3000
_DIRECTIVE_SUMMARY_MAX_CHARS = 800
_FAST_LANE_DIRECTIVE_INSTRUCTION_MAX_CHARS = 500
_FAST_LANE_DIRECTIVE_SUMMARY_MAX_CHARS = 240
_FAST_LANE_MEMORY_STAGES = {
    "operator_plan",
    "plan_review",
    "cache",
    "repair",
    "code_generation",
    "evidence_review",
}


_MEMORY_STAGE_TARGETS: dict[str, set[str]] = {
    "direct_answer": {"direct_answer", "final_answer"},
    "clarification": {"clarification", "direct_answer"},
    "operator_plan": {"operator_plan"},
    "plan_review": {"plan_review"},
    "cache": {"cache"},
    "repair": {"repair", "operator_plan"},
    "code_generation": {"code_generation", "operator_plan"},
    "evidence_review": {"evidence_review", "plan_review"},
    "final_answer": {"final_answer"},
    "answer_judge": {"answer_judge", "final_answer"},
    "followup": {"direct_answer", "final_answer", "operator_plan"},
    "classification": set(),
    "decomposition": set(),
}


def _stable_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, indent=2, default=str)
    except Exception:
        return str(value)


def _entry_dict(item: Any) -> dict[str, Any] | None:
    """Return a JSON-style memory entry dict."""

    if isinstance(item, MemoryEntry):
        return item.model_dump(mode="json")
    if isinstance(item, MemoryRetrievalMatch):
        return item.entry.model_dump(mode="json")
    if isinstance(item, dict):
        if isinstance(item.get("entry"), dict):
            return dict(item["entry"])
        return dict(item)
    return None


def _match_details_by_id(raw: Any) -> dict[str, dict[str, Any]]:
    """Return retrieval diagnostics keyed by memory id."""

    details: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, list):
        return details
    for item in raw:
        if isinstance(item, MemoryRetrievalMatch):
            payload = item.model_dump(mode="json")
        elif isinstance(item, dict):
            payload = item
        else:
            continue
        entry = payload.get("entry") if isinstance(payload.get("entry"), dict) else payload
        memory_id = str(entry.get("memory_id") or payload.get("memory_id") or "").strip()
        if memory_id:
            details[memory_id] = dict(payload)
    return details


def _directive_strength(entry: dict[str, Any]) -> str:
    """Choose a prompt strength from the user-authored memory text."""

    text = " ".join(
        [
            str(entry.get("instruction") or ""),
            str(entry.get("summary") or ""),
            " ".join(str(value) for value in entry.get("blocked_examples") or []),
        ]
    ).lower()
    strong_markers = (
        "always",
        "answer exactly",
        "before any",
        "before running",
        "do not",
        "don't",
        "must",
        "never",
        "only if",
        "unless explicitly",
    )
    if any(marker in text for marker in strong_markers):
        return "required_unless_conflict"
    if (
        entry.get("task_type")
        or entry.get("tool_type")
        or entry.get("intent_type")
        or entry.get("tags")
    ):
        return "must_consider"
    return "advisory"


def _directive_targets(entry: dict[str, Any]) -> list[str]:
    """Return the stages that should see this memory."""

    kind = str(entry.get("memory_kind") or "task_memory")
    text = f"{entry.get('instruction') or ''} {entry.get('summary') or ''}".lower()
    if kind == "validation_policy":
        return ["plan_review"]
    clarification_markers = (
        "ask the user",
        "ask user",
        "ask before",
        "clarify",
        "clarification",
        "provided by the user",
        "user provided",
        "user must provide",
        "not provided",
        "missing information",
    )
    if any(marker in text for marker in clarification_markers):
        return ["clarification", "operator_plan", "plan_review"]
    if "answer exactly" in text or "direct answer" in text:
        return ["direct_answer", "clarification", "final_answer"]
    final_answer_markers = (
        "final answer",
        "answer format",
        "response format",
        "respond with",
        "use markdown",
        "markdown table",
        "include a table",
    )
    if any(marker in text for marker in final_answer_markers):
        return ["direct_answer", "final_answer", "answer_judge"]
    return ["operator_plan", "plan_review", "cache"]


def _directive_payload(item: Any) -> dict[str, Any] | None:
    if isinstance(item, MemoryDirective):
        item = item.model_dump(mode="json")
    if not isinstance(item, dict):
        return None
    return {
        "memory_id": str(item.get("memory_id") or ""),
        "instruction": str(item.get("instruction") or "")[:_DIRECTIVE_INSTRUCTION_MAX_CHARS],
        "memory_kind": str(item.get("memory_kind") or "task_memory"),
        "applies_to": list(item.get("applies_to") or [])[:8],
        "strength": str(item.get("strength") or "advisory"),
        "summary": str(item.get("summary") or "")[:_DIRECTIVE_SUMMARY_MAX_CHARS],
        "safe_examples": list(item.get("safe_examples") or [])[:6],
        "blocked_examples": list(item.get("blocked_examples") or [])[:6],
        "match_reasons": list(item.get("match_reasons") or [])[:8],
        "score": int(item.get("score") or 0),
    }


def _stage_targets(stage: str | None) -> set[str] | None:
    if stage is None:
        return None
    normalized = str(stage or "").strip().lower()
    if not normalized:
        return None
    targets = set(_MEMORY_STAGE_TARGETS.get(normalized, {normalized}))
    if targets:
        targets.add(normalized)
    return targets


def _filter_directives_for_stage(
    directives: list[dict[str, Any]],
    *,
    stage: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    targets = _stage_targets(stage)
    if targets is None:
        return directives, []
    if not targets:
        return [], list(directives)
    rendered: list[dict[str, Any]] = []
    filtered: list[dict[str, Any]] = []
    for directive in directives:
        applies_to = {str(value).strip().lower() for value in directive.get("applies_to") or []}
        if applies_to & targets:
            rendered.append(directive)
        else:
            filtered.append(directive)
    return rendered, filtered


def _fast_lane_report_memory_stage(context: dict[str, Any], stage: str | None) -> bool:
    del context, stage
    return False


def _compact_directives_for_fast_lane_report(
    context: dict[str, Any],
    directives: list[dict[str, Any]],
    *,
    stage: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep fast-lane report prompts focused on current contract, not old examples."""

    if not _fast_lane_report_memory_stage(context, stage):
        return directives, []
    compacted: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    for directive in directives:
        strength = str(directive.get("strength") or "").strip()
        if strength != "required_unless_conflict":
            omitted.append(directive)
            continue
        if len(compacted) >= 3:
            omitted.append(directive)
            continue
        compacted.append(
            {
                "memory_id": str(directive.get("memory_id") or ""),
                "instruction": str(directive.get("instruction") or "")[
                    :_FAST_LANE_DIRECTIVE_INSTRUCTION_MAX_CHARS
                ],
                "memory_kind": str(directive.get("memory_kind") or "task_memory"),
                "applies_to": list(directive.get("applies_to") or [])[:8],
                "strength": strength,
                "summary": str(directive.get("summary") or "")[
                    :_FAST_LANE_DIRECTIVE_SUMMARY_MAX_CHARS
                ],
                "safe_examples": [],
                "blocked_examples": [],
                "match_reasons": list(directive.get("match_reasons") or [])[:3],
                "score": int(directive.get("score") or 0),
                "fast_lane_compacted": True,
            }
        )
    return compacted, omitted


def _record_memory_prompt_render(
    user_request: UserRequest,
    *,
    stage: str | None,
    rendered: list[dict[str, Any]],
    filtered: list[dict[str, Any]],
) -> None:
    if stage is None:
        return
    normalized = str(stage or "").strip().lower()
    if not normalized:
        return
    context = user_request.session_context
    rendered_ids = [
        str(item.get("memory_id") or "")
        for item in rendered
        if str(item.get("memory_id") or "").strip()
    ]
    filtered_ids = [
        str(item.get("memory_id") or "")
        for item in filtered
        if str(item.get("memory_id") or "").strip()
    ]
    active_scope = context.get("agent_memory_active_scope")
    active_scope_id = (
        str(active_scope.get("scope_id") or "").strip()
        if isinstance(active_scope, dict)
        else ""
    )
    if active_scope_id:
        record_scope_prompt_render(
            user_request,
            stage=normalized,
            scope_id=active_scope_id,
            rendered_ids=rendered_ids,
            filtered_ids=filtered_ids,
        )
    stage_ids = context.setdefault("agent_memory_rendered_stage_ids", {})
    if isinstance(stage_ids, dict):
        stage_ids[normalized] = rendered_ids
    filtered_stage_ids = context.setdefault("agent_memory_filtered_stage_ids", {})
    if isinstance(filtered_stage_ids, dict):
        filtered_stage_ids[normalized] = filtered_ids
    if filtered_ids and not rendered_ids:
        notes = context.setdefault("agent_memory_stage_filter_notes", {})
        if isinstance(notes, dict):
            notes[normalized] = {
                "rendered_count": 0,
                "filtered_count": len(filtered_ids),
                "filtered_memory_ids": filtered_ids,
                "scope_id": active_scope_id,
            }
    trace = user_request.safety_context.get("planning_trace")
    metadata = getattr(trace, "metadata", None)
    if not isinstance(metadata, dict):
        return
    metadata_stage_ids = metadata.setdefault("agent_memory_rendered_stage_ids", {})
    if isinstance(metadata_stage_ids, dict):
        metadata_stage_ids[normalized] = rendered_ids
    metadata_filtered_stage_ids = metadata.setdefault("agent_memory_filtered_stage_ids", {})
    if isinstance(metadata_filtered_stage_ids, dict):
        metadata_filtered_stage_ids[normalized] = filtered_ids
    if filtered_ids and not rendered_ids:
        metadata_notes = metadata.setdefault("agent_memory_stage_filter_notes", {})
        if isinstance(metadata_notes, dict):
            metadata_notes[normalized] = {
                "rendered_count": 0,
                "filtered_count": len(filtered_ids),
                "filtered_memory_ids": filtered_ids,
                "scope_id": active_scope_id,
            }


def memory_directives_from_entries(
    entries: list[Any],
    *,
    match_details: list[Any] | None = None,
) -> list[MemoryDirective]:
    """Convert retrieved entries into runtime-only prompt directives."""

    diagnostics = _match_details_by_id(match_details)
    directives: list[MemoryDirective] = []
    seen: set[str] = set()
    for item in entries[:10]:
        entry = _entry_dict(item)
        if not entry:
            continue
        memory_id = str(entry.get("memory_id") or "").strip()
        instruction = str(entry.get("instruction") or "").strip()
        if not memory_id or not instruction or memory_id in seen:
            continue
        seen.add(memory_id)
        match = diagnostics.get(memory_id, {})
        match_reasons = list(match.get("match_reasons") or [])
        if not match_reasons and isinstance(match.get("entry"), dict):
            match_reasons = list(match.get("match_reasons") or [])
        directives.append(
            MemoryDirective(
                memory_id=memory_id,
                instruction=instruction[:_DIRECTIVE_INSTRUCTION_MAX_CHARS],
                memory_kind=str(  # type: ignore[arg-type]
                    entry.get("memory_kind") or "task_memory"
                ),
                applies_to=_directive_targets(entry),  # type: ignore[arg-type]
                strength=_directive_strength(entry),  # type: ignore[arg-type]
                summary=str(entry.get("summary") or "")[:_DIRECTIVE_SUMMARY_MAX_CHARS],
                safe_examples=list(entry.get("safe_examples") or [])[:6],
                blocked_examples=list(entry.get("blocked_examples") or [])[:6],
                match_reasons=match_reasons[:8],
                score=int(match.get("score") or 0),
            )
        )
    return directives


def memory_directives_for_prompt_stage(
    user_request: UserRequest,
    *,
    stage: str | None = None,
) -> list[dict[str, Any]]:
    """Return bounded memory directives relevant to one prompt stage."""

    context = dict(user_request.session_context or {})
    scoped_raw_directives, _ = active_scope_raw_directives(context, stage=stage)
    if scoped_raw_directives is not None:
        scoped_directives: list[dict[str, Any]] = []
        for item in scoped_raw_directives[:10]:
            directive = _directive_payload(item)
            if directive is not None:
                scoped_directives.append(directive)
        rendered, filtered = _filter_directives_for_stage(scoped_directives, stage=stage)
        rendered, fast_lane_filtered = _compact_directives_for_fast_lane_report(
            context,
            rendered,
            stage=stage,
        )
        filtered = [*filtered, *fast_lane_filtered]
        _record_memory_prompt_render(
            user_request,
            stage=stage,
            rendered=rendered,
            filtered=filtered,
        )
        return rendered
    if scoped_memory_enabled(context):
        _record_memory_prompt_render(
            user_request,
            stage=stage,
            rendered=[],
            filtered=[],
        )
        return []
    raw_directives = context.get("agent_memory_directives")
    directives: list[dict[str, Any]] = []
    if isinstance(raw_directives, list) and raw_directives:
        for item in raw_directives[:10]:
            directive = _directive_payload(item)
            if directive is not None:
                directives.append(directive)
    raw = context.get("agent_memory")
    if not directives and isinstance(raw, list) and raw:
        directives = [
            directive.model_dump(mode="json")
            for directive in memory_directives_from_entries(
                raw,
                match_details=(
                    context.get("agent_memory_matches")
                    if isinstance(context.get("agent_memory_matches"), list)
                    else None
                ),
            )
        ]
    rendered, filtered = _filter_directives_for_stage(directives, stage=stage)
    rendered, fast_lane_filtered = _compact_directives_for_fast_lane_report(
        context,
        rendered,
        stage=stage,
    )
    filtered = [*filtered, *fast_lane_filtered]
    _record_memory_prompt_render(
        user_request,
        stage=stage,
        rendered=rendered,
        filtered=filtered,
    )
    return rendered


def memory_prompt_lines_from_context(
    user_request: UserRequest,
    *,
    stage: str | None = None,
) -> list[str]:
    """Return bounded memory guidance lines from the request context."""

    directives = memory_directives_for_prompt_stage(user_request, stage=stage)
    if not directives:
        return []
    return [
        "User-approved persistent memory constraints for this request:",
        _stable_json(directives),
        (
            "Follow required_unless_conflict memory unless it conflicts with the current user "
            "request, live runtime evidence, typed validation contracts, approvals, "
            "or hard safety policy."
        ),
        (
            "If an operator plan cannot follow a memory constraint, explain why in plan review "
            "or repair fields before continuing."
        ),
        (
            "Do not treat memory as evidence that a command ran or that current machine state "
            "was observed."
        ),
    ]
