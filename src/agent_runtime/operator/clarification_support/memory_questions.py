"""Memory-forced clarification helpers."""

from __future__ import annotations

from .common import *
from .policy_gates import *

def _memory_user_input_directive_text(directive: dict[str, Any]) -> str:
    return " ".join(
        [
            str(directive.get("instruction") or ""),
            str(directive.get("summary") or ""),
            " ".join(str(value) for value in directive.get("blocked_examples") or []),
        ]
    ).strip()

def _memory_requires_user_clarification(directive: dict[str, Any]) -> bool:
    text = _normalized_policy_text(_memory_user_input_directive_text(directive))
    if not text:
        return False
    if any(marker in text for marker in _MEMORY_NEGATED_ASK_MARKERS):
        return False
    return bool(
        any(marker in text for marker in _MEMORY_USER_ASK_MARKERS)
        and any(marker in text for marker in _MEMORY_USER_ASK_GATING_MARKERS)
    )

def _extract_memory_missing_information(directive: dict[str, Any]) -> str:
    """Best-effort subject extraction for user-ask memory directives."""

    text = str(directive.get("instruction") or directive.get("summary") or "").strip()
    patterns = (
        r"\bif\s+(?:an?\s+|the\s+)?(?P<subject>[^.,;:]+?)\s+"
        r"(?:is|are|was|were)\s+not\s+(?:provided|specified|included|given)\b",
        r"\bask\s+(?:the\s+)?user\s+for\s+(?:an?\s+|the\s+)?(?P<subject>[^.,;:]+?)\s+"
        r"(?:before|prior\s+to|unless|when|if)\b",
        r"\bprompt\s+(?:the\s+)?user\s+for\s+(?:an?\s+|the\s+)?(?P<subject>[^.,;:]+?)\s+"
        r"(?:before|prior\s+to|unless|when|if)\b",
        r"\buser\s+must\s+provide\s+(?:an?\s+|the\s+)?(?P<subject>[^.,;:]+?)\s+"
        r"(?:before|prior\s+to|unless|when|if)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        subject = str(match.group("subject") or "").strip(" .,:;\"'")
        subject = re.sub(r"\s+in\s+quotes?\b", "", subject, flags=re.IGNORECASE).strip()
        subject = re.sub(r"\s+", " ", subject)
        if subject:
            return subject
    summary = str(directive.get("summary") or "").strip(" .")
    if summary:
        return summary[:120]
    return "the missing user-provided value"

def _record_skipped_memory_forced_clarification(
    user_request: UserRequest,
    *,
    directive: dict[str, Any],
    missing_information: str,
    reason: str = "memory_question_policy_not_applies",
    instruction: str | None = None,
    scope: str = "llm_policy_not_applicable",
) -> None:
    context = user_request.session_context
    if not isinstance(context, dict):
        return
    notes = context.setdefault("operator_policy_notes", [])
    if not isinstance(notes, list):
        return
    scope_id, stage, task_id = memory_scope_tuple(context)
    memory_id = str(directive.get("memory_id") or "")
    notes.append(
        {
            "phase": "memory_question_policy",
            "reason": reason,
            "instruction": instruction
            or (
                "A memory-required clarification was judged not relevant to this "
                "request scope. Continue without asking for that missing memory field "
                "unless the current request explicitly needs it."
            ),
            "memory_id": memory_id,
            "missing_information": missing_information,
            "scope": scope,
            "memory_scope_id": scope_id,
            "stage": stage,
            "task_id": task_id,
        }
    )
    record_scope_memory_question_skip(
        user_request,
        scope_id=scope_id,
        stage=stage,
        task_id=task_id,
        memory_id=memory_id,
    )

def _memory_forced_clarification_candidates(
    user_request: UserRequest,
) -> list[tuple[OperatorClarificationRequest, dict[str, Any]]]:
    """Return unanswered memory clarification candidates."""

    candidates: list[tuple[OperatorClarificationRequest, dict[str, Any]]] = []
    scope_id, stage, task_id = memory_scope_tuple(dict(user_request.session_context or {}))
    for directive in memory_directives_for_prompt_stage(user_request, stage="clarification"):
        if not _memory_requires_user_clarification(directive):
            continue
        directive_text = _memory_user_input_directive_text(directive)
        missing_information = _extract_memory_missing_information(directive)
        display_missing = missing_information[0].upper() + missing_information[1:]
        request = OperatorClarificationRequest(
            question=f"What {missing_information} should I use before continuing?",
            reason=(
                "Persistent memory for this request requires user-provided input "
                "before the operator continues."
            ),
            missing_information=display_missing,
            options=[],
            allow_freeform=True,
            confidence=1.0,
        )
        candidates.append(
            (
                request,
                {
                    "source": "memory_directive",
                    "memory_id": str(directive.get("memory_id") or ""),
                    "instruction": str(directive.get("instruction") or "")[:500],
                    "missing_information": missing_information,
                    "memory_scope_id": scope_id,
                    "stage": stage,
                    "task_id": task_id,
                },
            )
        )
    return candidates

def _skipped_memory_question_ids(user_request: UserRequest) -> set[str]:
    """Return memory IDs already rejected for the current clarification scope."""

    raw = dict(user_request.session_context or {}).get("operator_policy_notes")
    if not isinstance(raw, list):
        return set()
    current_scope_id, current_stage, current_task_id = memory_scope_tuple(
        dict(user_request.session_context or {})
    )
    skipped: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("phase") or "") != "memory_question_policy":
            continue
        note_scope_id = str(entry.get("memory_scope_id") or "")
        note_stage = str(entry.get("stage") or "")
        note_task_id = str(entry.get("task_id") or "")
        if current_scope_id and note_scope_id and note_scope_id != current_scope_id:
            continue
        if current_stage and note_stage and note_stage != current_stage:
            continue
        if current_task_id and note_task_id and note_task_id != current_task_id:
            continue
        memory_id = str(entry.get("memory_id") or "").strip()
        if memory_id:
            skipped.add(memory_id)
    return skipped

def _clarification_memory_prompt_lines(user_request: UserRequest) -> list[str]:
    """Render clarification memory after dropping directives rejected as out of scope."""

    directives = memory_directives_for_prompt_stage(user_request, stage="clarification")
    if not directives:
        return []
    skipped_ids = _skipped_memory_question_ids(user_request)
    if skipped_ids:
        directives = [
            directive
            for directive in directives
            if str(directive.get("memory_id") or "") not in skipped_ids
        ]
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

__all__ = [name for name in globals() if not name.startswith("__")]
