"""Operator clarification policy gate helpers."""

from __future__ import annotations

from .common import *

def _mask_prompt_literal_regions(text: str) -> str:
    from agent_runtime.operator.memory_compliance import (
        _mask_prompt_literal_regions as mask_prompt_literal_regions,
    )

    return mask_prompt_literal_regions(text)

def _normalized_task_trigger_reason(task: Any, *, scope: str) -> str:
    from agent_runtime.operator.memory_compliance import (
        _normalized_task_trigger_reason as normalized_task_trigger_reason,
    )

    return normalized_task_trigger_reason(task, scope=scope)

def _operator_intent_tasks_from_context(context: dict[str, Any]) -> list[dict[str, Any]]:
    from agent_runtime.operator.memory_compliance import (
        _operator_intent_tasks_from_context as operator_intent_tasks_from_context,
    )

    return operator_intent_tasks_from_context(context)

def _normalized_policy_text(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9_./-]+", str(value or "").lower()))

def _clarification_packet_text(request: OperatorClarificationRequest) -> str:
    option_text = " ".join(
        f"{option.label} {option.description}" for option in request.options
    )
    return "\n".join(
        [
            str(request.question or ""),
            str(request.reason or ""),
            str(request.missing_information or ""),
            option_text,
        ]
    )

def _clarification_asks_for_hardware_identity(request: OperatorClarificationRequest) -> bool:
    packet = _normalized_policy_text(_clarification_packet_text(request))
    if not packet:
        return False
    words = set(packet.split())
    return bool(words & _HARDWARE_TARGET_CLARIFICATION_WORDS) and bool(
        words & _HARDWARE_IDENTITY_CLARIFICATION_WORDS
    )

def _clarification_asks_for_spelling_confirmation(request: OperatorClarificationRequest) -> bool:
    packet = _normalized_policy_text(_clarification_packet_text(request))
    if not packet:
        return False
    words = set(packet.split())
    return bool(words & _SPELLING_CLARIFICATION_WORDS) or "correct spelling" in packet

def _clarification_repeats_prior_answer(
    user_request: UserRequest,
    request: OperatorClarificationRequest,
) -> bool:
    raw = dict(user_request.session_context or {}).get("clarifications")
    if not isinstance(raw, list):
        return False
    question = _normalized_policy_text(request.question)
    missing = _normalized_policy_text(request.missing_information)
    packet = _normalized_policy_text(_clarification_packet_text(request))
    for entry in raw[-10:]:
        if not isinstance(entry, dict):
            continue
        prior_question = _normalized_policy_text(entry.get("question"))
        prior_missing = _normalized_policy_text(entry.get("missing_information"))
        prior_answer = _normalized_policy_text(entry.get("answer"))
        if missing and prior_missing and missing == prior_missing:
            return True
        if question and prior_question and question == prior_question:
            return True
        if prior_answer and prior_answer in {"current", "current branch", "current git branch"}:
            if "branch" in packet:
                return True
        if prior_answer and prior_answer in {"current directory", "current working directory", "cwd"}:
            if "directory" in packet or "cwd" in packet:
                return True
    return False

def _clarification_asks_for_user_choice(
    user_request: UserRequest,
    request: OperatorClarificationRequest,
) -> bool:
    """Return whether this clarification asks for non-discoverable user intent."""

    packet = _normalized_policy_text(_clarification_packet_text(request))
    prompt = _normalized_policy_text(user_request.raw_prompt)
    if not packet:
        return False
    words = set(packet.split())
    prompt_words = set(prompt.split())
    combined_words = words | prompt_words
    if (
        combined_words & _DISCOVERABLE_RESOURCE_WORDS
        and combined_words & _DISCOVERABLE_EXISTING_TARGET_WORDS
        and words & _DISCOVERABLE_IDENTITY_WORDS
    ):
        return False
    if "branch" in words and not (prompt_words & {"create", "new", "rename"}):
        return False
    has_creation_intent = bool(prompt_words & _NEW_RESOURCE_INTENT_WORDS)
    asks_for_choice = bool(words & _USER_CHOICE_CLARIFICATION_WORDS) or any(
        phrase in packet
        for phrase in (
            "what should",
            "what do you want",
            "would you like",
            "which option",
            "which version",
        )
    )
    if has_creation_intent and asks_for_choice:
        return True
    return any(
        phrase in packet
        for phrase in (
            "desired version",
            "desired name",
            "new environment name",
            "new resource name",
            "python version",
            "project name",
            "package version",
            "template choice",
        )
    )

def _clarification_asks_for_discoverable_state(
    user_request: UserRequest,
    request: OperatorClarificationRequest,
) -> bool:
    packet = _normalized_policy_text(_clarification_packet_text(request))
    prompt = _normalized_policy_text(user_request.raw_prompt)
    if not packet:
        return False
    if _clarification_asks_for_hardware_identity(request):
        return True
    if _clarification_asks_for_user_choice(user_request, request):
        return False
    if any(phrase in packet for phrase in _DISCOVERABLE_CLARIFICATION_PHRASES):
        if "branch" not in packet:
            return True
        branch_creation_prompt = bool(
            re.search(r"\b(create|new|name|rename)\b.*\bbranch\b", prompt)
        )
        if not branch_creation_prompt:
            return True
    if _prompt_requires_runtime_observation(packet):
        return True
    return False

def _clarification_asks_for_provided_user_macro_input(
    user_request: UserRequest,
    request: OperatorClarificationRequest,
) -> bool:
    """Return whether a clarification asks for input already covered by a macro."""

    has_typein = bool(_typein_macro_input_name_from_request(user_request))
    if not has_typein:
        return False
    packet = _normalized_policy_text(_clarification_packet_text(request))
    if not packet:
        return False
    words = set(packet.split())
    return bool(words & _USER_MACRO_COVERED_CLARIFICATION_WORDS)

def _review_operator_clarification_policy(
    user_request: UserRequest,
    request: OperatorClarificationRequest,
) -> _ClarificationPolicyReview:
    """Return whether a user clarification pause is admissible.

    This is an architectural guard, not a task-specific fallback. It only
    decides whether asking the user is allowed. Discoverable runtime facts must
    be inspected by the operator plan instead of turning into user questions.
    """

    if _clarification_repeats_prior_answer(user_request, request):
        return _ClarificationPolicyReview(
            allowed=False,
            reason="prior_clarification_answer",
            instruction=(
                "A previous clarification already answered this missing information. "
                "Treat the answer as an explicit user constraint and continue without asking again."
            ),
        )
    if _clarification_asks_for_spelling_confirmation(request):
        return _ClarificationPolicyReview(
            allowed=False,
            reason="spelling_confirmation",
            instruction=(
                "Do not pause only to confirm an obvious spelling or typo correction. "
                "Proceed with the most likely normalized wording from context, and ask later "
                "only if an exact user-chosen identifier is truly required."
            ),
        )
    if _clarification_asks_for_discoverable_state(user_request, request):
        return _ClarificationPolicyReview(
            allowed=False,
            reason="runtime_discoverable_fact",
            instruction=(
                "The proposed clarification asks for local runtime state that the operator can "
                "safely discover. Continue by planning a read-only inspection/probe or by "
                "recomputing that state inside the mutating action instead of asking the user."
            ),
        )
    if _clarification_asks_for_provided_user_macro_input(user_request, request):
        return _ClarificationPolicyReview(
            allowed=False,
            reason="user_macro_provided_input",
            instruction=(
                "A deterministic typein macro already supplies this requested input. "
                "Continue without asking again. For real terminal prompts, plan a "
                "shell_command with interaction_mode may_prompt, stdin_mode none, and no "
                "password/secret inputs; the runtime will type the macro value into the "
                "terminal when input is requested."
            ),
        )
    return _ClarificationPolicyReview(allowed=True)

def _append_operator_policy_note(
    user_request: UserRequest,
    *,
    phase: str,
    request: OperatorClarificationRequest,
    review: _ClarificationPolicyReview,
) -> None:
    raw = user_request.session_context.get("operator_policy_notes")
    notes = list(raw) if isinstance(raw, list) else []
    notes.append(
        {
            "phase": phase,
            "reason": review.reason,
            "instruction": review.instruction,
            "question": request.question,
            "missing_information": request.missing_information,
        }
    )
    user_request.session_context["operator_policy_notes"] = notes[-5:]

__all__ = [name for name in globals() if not name.startswith("__")]
