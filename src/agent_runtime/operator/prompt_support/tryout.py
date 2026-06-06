"""Retired tryout prompt builders."""

from __future__ import annotations

# ruff: noqa: F401,F403,F405

from agent_runtime.operator.prompt_support.common import *
from agent_runtime.operator.prompt_support.context import *

def build_operator_tryout_capsule_prompt(
    user_request: UserRequest,
    feedback: list[dict[str, Any]] | None = None,
) -> str:
    """Build the retired operator tryout capsule drafting prompt."""

    lines = prompt_lines(
        "operator.tryout_capsule",
        {"schema_json": _stable_json(OperatorTryoutCapsuleBatch.model_json_schema())},
    )
    if feedback:
        lines.extend(
            [
                "Retired tryout feedback from prior capsule attempt:",
                _stable_json(feedback),
                "Keep capsule output empty; decomposition is responsible for executable work.",
            ]
        )
    intent_block = user_request.session_context.get("operator_intent_block")
    if isinstance(intent_block, dict):
        lines.extend(
            [
                "Agent operator intent block:",
                _stable_json(_operator_prompt_intent_block(intent_block)),
                "Use this block as semantic intent only; keep the retired tryout capsule output empty.",
            ]
        )
    lines.extend(["User prompt:", user_request.raw_prompt])
    return "\n".join(lines)


def build_operator_tryout_result_review_prompt(
    user_request: UserRequest,
    plan: OperatorPlan,
    records: list[OperatorExecutionRecord],
) -> str:
    """Build the retired operator tryout result acceptance prompt."""

    return "\n".join(
        [
            *prompt_lines(
                "operator.tryout_result_review",
                {"schema_json": _stable_json(OperatorTryoutResultReview.model_json_schema())},
            ),
            "User prompt:",
            user_request.raw_prompt,
            "Retired tryout plan:",
            plan.model_dump_json(indent=2),
            "Execution records:",
            _stable_json({"records": records}),
        ]
    )


__all__ = [name for name in globals() if not name.startswith("__")]
