"""Clarification resolution and prompt helpers."""

from __future__ import annotations

from .common import *
from .memory_questions import *
from .policy_gates import *

def _clarification_strategy_from_request(user_request: UserRequest) -> str:
    return _agent_clarification_mode_from_request(user_request)

def _agent_clarification_mode_from_request(user_request: UserRequest) -> str:
    session_context = dict(user_request.session_context or {})
    safety_context = dict(user_request.safety_context or {})
    return normalize_agent_clarification_mode(
        session_context.get("agent_clarification_mode")
        or safety_context.get("agent_clarification_mode"),
    )

def _clarification_strategy_policy(strategy: str) -> str:
    mode = normalize_agent_clarification_mode(strategy, legacy_strategy=strategy)
    return "\n".join(prompt_lines(f"clarification.mode_policy.{mode}"))

def _clarification_request_risk_flags(request: OperatorClarificationRequest) -> list[str]:
    flags: list[str] = []
    if request.secret_input or request.input_kind in {
        "password",
        "passphrase",
        "private_key",
        "token",
        "credential",
    }:
        flags.append("credential_needed")
    missing = str(request.missing_information or "").lower()
    question = str(request.question or "").lower()
    if "database parameter" in missing or "database parameter" in question:
        flags.append("ambiguous_database_profile")
    if re.search(r"\b(delete|remove|overwrite|drop|destroy|production|prod)\b", question):
        flags.append("destructive_target")
    return flags

def _clarification_resolution_context(user_request: UserRequest) -> dict[str, Any]:
    context = dict(user_request.session_context or {})
    sanitized: dict[str, Any] = {}
    for key in (
        "operator_intent_block",
        "operator_mode_label",
        "operator_display_label",
        "operator_policy_notes",
        "operator_self_brief",
        "operator_streaming_current_task",
        "clarifications",
    ):
        value = context.get(key)
        if value not in (None, "", [], {}):
            sanitized[key] = value
    return sanitized

def _clarification_options_payload(
    request: OperatorClarificationRequest,
) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = [
        option.model_dump(mode="json") for option in list(request.options or [])
    ]
    options.extend(
        {
            "option_id": choice.choice_id,
            "label": choice.label or choice.key,
            "description": choice.description,
            "normalized_key": choice.normalized_key,
            "tags": list(choice.tags),
            "group": choice.group,
            "score": choice.score,
            "match_reasons": list(choice.match_reasons),
        }
        for choice in list(request.parameter_choices or [])
    )
    return options

def _apply_clarification_resolution_to_request(
    user_request: UserRequest,
    resolution: AgentClarificationResolution,
) -> None:
    payload = resolution.model_dump(mode="json")
    context = user_request.session_context
    prior = list(context.get("agent_clarification_resolutions") or [])
    prior.append(payload)
    context["agent_clarification_resolutions"] = prior[-8:]
    context["agent_clarification_assumptions"] = [
        *list(context.get("agent_clarification_assumptions") or []),
        *list(resolution.assumptions or []),
    ][-24:]
    context["agent_clarification_selected_entities"] = [
        *list(context.get("agent_clarification_selected_entities") or []),
        *[entity.model_dump(mode="json") for entity in resolution.selected_entities],
    ][-24:]
    context["agent_clarification_execution_directives"] = [
        *list(context.get("agent_clarification_execution_directives") or []),
        *list(resolution.execution_directives or []),
    ][-24:]

def _operator_clarification_resolution_details(
    resolution: AgentClarificationResolution,
    *,
    phase: str,
) -> dict[str, Any]:
    payload = resolution.model_dump(mode="json")
    payload["phase"] = phase
    return payload

def build_operator_clarification_prompt(
    user_request: UserRequest,
    conversation_context: dict[str, Any] | None = None,
    failure_context: dict[str, Any] | None = None,
) -> str:
    """Build a typed one-question clarification gate prompt."""

    mode = _agent_clarification_mode_from_request(user_request)
    mode_label = str(user_request.session_context.get("operator_mode_label") or "Conversational").strip()
    intent_block = user_request.session_context.get("operator_intent_block")
    lines = [
        *prompt_lines(
            "operator.clarification",
            {
                "mode_label": mode_label,
                "mode": mode,
                "mode_policy": _clarification_strategy_policy(mode),
                "schema_json": _stable_json(OperatorClarificationDecision.model_json_schema()),
            },
        ),
        *_operator_discoverability_lines(),
        *_clarification_memory_prompt_lines(user_request),
        *_operator_user_hint_lines(),
        *_operator_user_macro_lines(user_request),
        *_operator_online_lookup_lines(user_request),
        *_operator_literal_payload_lines(user_request),
        *_operator_policy_note_lines(user_request),
        *_operator_clarification_lines(user_request),
    ]
    if isinstance(intent_block, dict):
        lines.extend(["Agent operator intent block:", _stable_json(intent_block)])
    if isinstance(conversation_context, dict):
        lines.extend(["Conversation context JSON:", _stable_json(conversation_context)])
    if isinstance(failure_context, dict):
        lines.extend(
            [
                "Runtime failure or completion context:",
                _stable_json(failure_context),
                "If a safe next action depends on user intent rather than runtime output, ask the user instead of guessing.",
            ]
        )
    lines.extend(["User prompt:", user_request.raw_prompt])
    return "\n".join(lines)

__all__ = [name for name in globals() if not name.startswith("__")]
