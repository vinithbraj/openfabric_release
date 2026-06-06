"""Shared clarification-mode policy and typed LLM resolution helpers."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_runtime.llm.structured_call import structured_call
from agent_runtime.prompts import prompt_lines

AgentClarificationMode = Literal["auto_pilot", "balanced", "pedantic"]

CLARIFICATION_MODES = {"auto_pilot", "balanced", "pedantic"}
LEGACY_CLARIFICATION_STRATEGY_TO_MODE = {
    "material_gaps": "auto_pilot",
    "llm_decides": "balanced",
    "ask_any_missing": "pedantic",
}
MODE_TO_LEGACY_CLARIFICATION_STRATEGY = {
    "auto_pilot": "material_gaps",
    "balanced": "llm_decides",
    "pedantic": "ask_any_missing",
}

_CREDENTIAL_RISK_FLAGS = {
    "credential_needed",
    "password_needed",
    "passphrase_needed",
    "private_key_needed",
    "token_needed",
    "secret_needed",
}


class AgentClarificationSelectedEntity(BaseModel):
    """Typed entity chosen by the clarification resolver."""

    model_config = ConfigDict(extra="forbid")

    entity_type: str = Field(default="", max_length=80)
    name: str = Field(default="", max_length=240)
    value: str = Field(default="", max_length=1000)
    source: str = Field(default="", max_length=160)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class AgentClarificationResolution(BaseModel):
    """Typed decision that either asks the user or drives the agent forward."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["ask_user", "continue_with_assumption"]
    mode: AgentClarificationMode = "balanced"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    selected_entities: list[AgentClarificationSelectedEntity] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list, max_length=12)
    execution_directives: list[str] = Field(default_factory=list, max_length=12)
    user_question: str = Field(default="", max_length=1000)
    risk_flags: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_resolution(self) -> "AgentClarificationResolution":
        if self.decision == "ask_user" and not self.user_question.strip():
            raise ValueError("ask_user requires user_question.")
        if self.decision == "continue_with_assumption" and (
            not self.assumptions and not self.selected_entities and not self.execution_directives
        ):
            raise ValueError(
                "continue_with_assumption requires assumptions, selected_entities, "
                "or execution_directives."
            )
        return self


def normalize_agent_clarification_mode(
    value: Any = None,
    *,
    legacy_strategy: Any = None,
) -> AgentClarificationMode:
    """Normalize the single clarification mode, accepting the deprecated strategy names."""

    raw = str(value or "").strip().lower()
    if raw in CLARIFICATION_MODES:
        return raw  # type: ignore[return-value]
    legacy = str(legacy_strategy or "").strip().lower()
    if legacy in LEGACY_CLARIFICATION_STRATEGY_TO_MODE:
        return LEGACY_CLARIFICATION_STRATEGY_TO_MODE[legacy]  # type: ignore[return-value]
    if raw in LEGACY_CLARIFICATION_STRATEGY_TO_MODE:
        return LEGACY_CLARIFICATION_STRATEGY_TO_MODE[raw]  # type: ignore[return-value]
    return "balanced"


def legacy_strategy_for_clarification_mode(mode: Any) -> str:
    """Return the deprecated operator strategy equivalent for compatibility surfaces."""

    normalized = normalize_agent_clarification_mode(mode)
    return MODE_TO_LEGACY_CLARIFICATION_STRATEGY[normalized]


def clarification_continue_threshold(mode: Any) -> float:
    normalized = normalize_agent_clarification_mode(mode)
    if normalized == "auto_pilot":
        return 0.8
    if normalized == "balanced":
        return 0.9
    return 1.01


def clarification_resolution_can_continue(
    resolution: AgentClarificationResolution,
    *,
    mode: Any,
) -> bool:
    """Gate typed LLM resolution so it cannot bypass hard user-input requirements."""

    normalized = normalize_agent_clarification_mode(mode)
    if normalized == "pedantic":
        return False
    if resolution.decision != "continue_with_assumption":
        return False
    if normalize_agent_clarification_mode(resolution.mode) != normalized:
        return False
    if resolution.confidence < clarification_continue_threshold(normalized):
        return False
    risk_flags = {str(flag or "").strip().lower() for flag in resolution.risk_flags}
    if risk_flags & _CREDENTIAL_RISK_FLAGS:
        return False
    return True


def fallback_ask_user_resolution(
    *,
    mode: Any,
    question: str,
    reason: str = "",
    risk_flags: list[str] | None = None,
    confidence: float = 0.0,
) -> AgentClarificationResolution:
    normalized = normalize_agent_clarification_mode(mode)
    return AgentClarificationResolution(
        decision="ask_user",
        mode=normalized,
        confidence=max(0.0, min(1.0, float(confidence or 0.0))),
        user_question=str(question or "What should I clarify before continuing?").strip(),
        risk_flags=[str(flag) for flag in list(risk_flags or []) if str(flag).strip()],
        reason=str(reason or "Clarification should be shown to the user.").strip(),
    )


def _bounded_json(value: Any, *, max_chars: int = 8000) -> str:
    try:
        rendered = json.dumps(value, ensure_ascii=True, sort_keys=True)
    except TypeError:
        rendered = json.dumps(str(value), ensure_ascii=True)
    if len(rendered) <= max_chars:
        return rendered
    return rendered[: max_chars - 32] + "...<truncated>"


def build_agent_clarification_resolution_prompt(
    *,
    mode: Any,
    user_prompt: str,
    proposed_question: str,
    missing_information: str,
    reason: str,
    options: list[dict[str, Any]] | None = None,
    candidates: list[dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
    risk_flags: list[str] | None = None,
) -> str:
    normalized = normalize_agent_clarification_mode(mode)
    return "\n".join(
        prompt_lines(
            "clarification.resolution",
            {
                "mode": normalized,
                "continue_threshold": f"{clarification_continue_threshold(normalized):.2f}",
                "schema_json": _bounded_json(
                    AgentClarificationResolution.model_json_schema(),
                    max_chars=12000,
                ),
                "user_prompt": str(user_prompt or "")[:12000],
                "proposed_question": str(proposed_question or "")[:2000],
                "missing_information": str(missing_information or "")[:500],
                "reason": str(reason or "")[:2000],
                "options_json": _bounded_json(options or [], max_chars=8000),
                "candidates_json": _bounded_json(candidates or [], max_chars=12000),
                "context_json": _bounded_json(context or {}, max_chars=12000),
                "risk_flags_json": _bounded_json(risk_flags or [], max_chars=2000),
            },
        )
    )


def resolve_agent_clarification(
    *,
    llm_client: Any,
    mode: Any,
    user_prompt: str,
    proposed_question: str,
    missing_information: str,
    reason: str = "",
    options: list[dict[str, Any]] | None = None,
    candidates: list[dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
    risk_flags: list[str] | None = None,
) -> AgentClarificationResolution:
    """Run the typed resolver; fallback to asking on unavailable or unusable output."""

    normalized = normalize_agent_clarification_mode(mode)
    if normalized == "pedantic":
        return fallback_ask_user_resolution(
            mode=normalized,
            question=proposed_question,
            reason="Pedantic clarification mode asks the user.",
            risk_flags=risk_flags,
        )
    prompt = build_agent_clarification_resolution_prompt(
        mode=normalized,
        user_prompt=user_prompt,
        proposed_question=proposed_question,
        missing_information=missing_information,
        reason=reason,
        options=options,
        candidates=candidates,
        context=context,
        risk_flags=risk_flags,
    )
    try:
        resolution = structured_call(llm_client, prompt, AgentClarificationResolution)
    except Exception as exc:
        return fallback_ask_user_resolution(
            mode=normalized,
            question=proposed_question,
            reason=f"Clarification resolver unavailable: {exc}",
            risk_flags=risk_flags,
        )
    if clarification_resolution_can_continue(resolution, mode=normalized):
        return resolution
    if resolution.decision == "ask_user":
        return resolution
    return fallback_ask_user_resolution(
        mode=normalized,
        question=proposed_question,
        reason=(
            "Typed clarification resolution did not meet the mode threshold or "
            "contained risk flags requiring user input."
        ),
        risk_flags=list(resolution.risk_flags or risk_flags or []),
        confidence=resolution.confidence,
    )
