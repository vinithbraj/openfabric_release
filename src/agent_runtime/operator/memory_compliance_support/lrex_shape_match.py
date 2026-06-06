"""Structured LR-EX shape matching helpers."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.llm.structured_call import structured_call
from agent_runtime.prompts import prompt_lines


class LrExShapeMatchDecision(BaseModel):
    """One structured decision for LR-EX semantic shape matching."""

    model_config = ConfigDict(extra="forbid")

    equivalent: bool = False
    selected_candidate_id: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    unsafe_to_reuse: bool = False
    prior_binding_map: dict[str, str] = Field(default_factory=dict)


def _stable_json(value: Any, *, max_chars: int = 12000) -> str:
    try:
        text = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str, indent=2)
    except Exception:
        text = repr(value)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"


def build_lrex_shape_match_prompt(
    *,
    current_shape: dict[str, Any],
    candidate_shapes: list[dict[str, Any]],
    prior_outputs: list[dict[str, Any]],
) -> str:
    """Return the compact judge prompt for LR-EX shape equivalence."""

    return "\n".join(
        [
            *prompt_lines("operator.lrex_shape_match"),
            "",
            "Current step JSON:",
            _stable_json(current_shape),
            "",
            "Cached candidates JSON:",
            _stable_json(candidate_shapes),
            "",
            "Available prior outputs JSON:",
            _stable_json(prior_outputs),
            "",
            "Return only JSON matching the supplied schema.",
        ]
    )


def judge_lrex_shape_match(
    llm_client: Any,
    *,
    current_shape: dict[str, Any],
    candidate_shapes: list[dict[str, Any]],
    prior_outputs: list[dict[str, Any]],
) -> LrExShapeMatchDecision:
    """Ask the configured LLM for one cheap LR-EX shape decision."""

    prompt = build_lrex_shape_match_prompt(
        current_shape=current_shape,
        candidate_shapes=candidate_shapes,
        prior_outputs=prior_outputs,
    )
    return structured_call(
        llm_client,
        prompt,
        LrExShapeMatchDecision,
        repair_attempts=0,
    )


__all__ = [
    "LrExShapeMatchDecision",
    "build_lrex_shape_match_prompt",
    "judge_lrex_shape_match",
]
