from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.operator.models import OperatorPolicyDecision, OperatorPolicySubject
from agent_runtime.operator.policy import (
    build_operator_policy_review_prompt,
    normalize_operator_policy_mode,
    operator_policy_cache_key,
    operator_policy_mode,
    review_operator_policy_subjects,
)


class FakePolicyLLM:
    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, prompt: str, schema: dict) -> dict:
        self.calls += 1
        assert "Review operator policy subjects." in prompt
        assert schema.get("title") == "OperatorPolicyReviewResult"
        return {
            "decisions": [
                {
                    "module": "effect",
                    "subject_id": "action_1",
                    "decision": "mutates_state",
                    "effect_intent": "mutates_state",
                    "risk_level": "high",
                    "requires_confirmation": True,
                    "confidence": 0.91,
                    "reason": "The action creates state.",
                    "evidence_refs": ["action_1"],
                    "repair_hint": "",
                }
            ]
        }


def test_operator_policy_response_schema_requires_uniform_fields() -> None:
    with pytest.raises(ValidationError):
        OperatorPolicyDecision.model_validate(
            {
                "module": "effect",
                "subject_id": "action_1",
                "decision": "mutates_state",
            }
        )


def test_operator_policy_review_batches_and_caches_decisions() -> None:
    subject = OperatorPolicySubject(
        module="effect",
        subject_id="action_1",
        task={"semantic_verb": "create"},
        action={"command": "custom-env-tool create poop1"},
    )
    llm = FakePolicyLLM()
    cache: dict = {}

    first = review_operator_policy_subjects(llm, [subject], request_id="req_1", cache=cache)
    second = review_operator_policy_subjects(llm, [subject], request_id="req_1", cache=cache)

    assert llm.calls == 1
    assert len(first.decisions) == 1
    assert first.decisions[0].effect_intent == "mutates_state"
    assert second.decisions[0] == first.decisions[0]
    assert operator_policy_cache_key(request_id="req_1", mode="llm", subject=subject) in cache


def test_operator_policy_helpers_normalize_and_prompt_subjects() -> None:
    subject = OperatorPolicySubject(
        module="memory_question",
        subject_id="memory_1",
        evidence=[{"exit_code": 1}],
    )

    assert normalize_operator_policy_mode("LLM") == "llm"
    assert normalize_operator_policy_mode("surprise") == "deterministic"
    assert operator_policy_mode(object(), "memory_question") == "llm"
    prompt = build_operator_policy_review_prompt([subject])
    assert '"module": "memory_question"' in prompt
    assert '"subject_id": "memory_1"' in prompt
    assert "Use decision ask_user only" in prompt
    assert "whether this memory's missing value is required for the specific current operation" in prompt
    assert "Shared words, tools, repositories, domains, or object families are not enough" in prompt
    assert "a Git commit-message memory is not required for reading the current branch" in prompt
