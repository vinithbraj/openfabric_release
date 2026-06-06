from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_runtime.capabilities import CapabilityRegistry
from agent_runtime.core.config import RuntimeConfig
from agent_runtime.core.orchestrator import AgentRuntime
from agent_runtime.execution.engine import ExecutionEngine
from agent_runtime.execution.result_store import InMemoryResultStore
from agent_runtime.input_pipeline.prompt_rephrase import (
    dropped_protected_spans,
    rephrase_prompt_preflight,
    validate_prompt_rephrase,
)
from agent_runtime.llm.proposals import PromptRephraseProposal
from agent_runtime.output_pipeline.orchestrator import OutputPipelineOrchestrator
from agent_runtime.settings_consolidation import effective_prompt_rephrase_enabled


class QueueLLM:
    model = "fake-rephrase"
    temperature = 0.0

    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.schemas: list[str] = []

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        self.schemas.append(str(schema.get("title") or ""))
        if not self.responses:
            raise AssertionError("Unexpected LLM call")
        return self.responses.pop(0)


def _classification_payload() -> dict[str, Any]:
    return {
        "prompt_type": "simple_question",
        "requires_tools": False,
        "likely_domains": [],
        "risk_level": "low",
        "needs_clarification": False,
        "clarification_question": None,
        "reason": "The prompt asks for a direct explanation.",
        "confidence": 0.95,
        "assumptions": [],
        "domain_evaluations": [],
    }


def _direct_answer_payload() -> dict[str, Any]:
    return {
        "answer": "Hello world is a simple example phrase.",
        "confidence": 0.95,
        "reason": "Direct answer.",
    }


def _runtime(tmp_path: Path, llm: QueueLLM, config: RuntimeConfig) -> AgentRuntime:
    registry = CapabilityRegistry()
    engine = ExecutionEngine(registry, config, InMemoryResultStore())
    return AgentRuntime(llm, registry, engine, OutputPipelineOrchestrator())


def test_preflight_rephrase_runs_before_classification_and_becomes_canonical(tmp_path: Path) -> None:
    original = "hey hey can you explain hello world, explain hello world, just once please"
    rephrased = "Explain hello world once."
    llm = QueueLLM(
        {
            "rephrased_prompt": rephrased,
            "changed": True,
            "preserved_requirements": ["explain hello world", "once"],
            "removed_duplication": ["repeated request to explain hello world"],
            "reason": "The original duplicated the same request.",
            "confidence": 0.93,
        },
        _classification_payload(),
        _direct_answer_payload(),
    )
    runtime = _runtime(
        tmp_path,
        llm,
        RuntimeConfig(workspace_root=str(tmp_path), prompt_rephrase_enabled=True),
    )

    response = runtime.handle_request(original)

    assert "Hello world" in response
    assert llm.schemas[:2] == ["PromptRephraseProposal", "PromptClassificationProposal"]
    assert rephrased in llm.prompts[1]
    trace = runtime.last_planning_trace
    assert trace is not None
    stages = [entry.stage for entry in trace.entries]
    assert stages.index("prompt_rephrase") < stages.index("prompt_classification")
    assert trace.raw_prompt == rephrased
    assert trace.metadata["original_user_prompt"] == original
    assert trace.metadata["prompt_rephrase"]["applied"] is True


def test_prompt_rephrase_disabled_skips_preflight(tmp_path: Path) -> None:
    llm = QueueLLM(_classification_payload(), _direct_answer_payload())
    runtime = _runtime(
        tmp_path,
        llm,
        RuntimeConfig(workspace_root=str(tmp_path), prompt_rephrase_enabled=False),
    )

    runtime.handle_request("explain hello world")

    assert llm.schemas[:2] == ["PromptClassificationProposal", "DirectAnswerProposal"]
    trace = runtime.last_planning_trace
    assert trace is not None
    assert trace.raw_prompt == "explain hello world"
    assert trace.metadata["prompt_rephrase_enabled"] is False
    assert trace.metadata["prompt_rephrase"]["fallback_reason"] == "disabled"


def test_prompt_rephrase_preserves_literals_macros_paths_and_dates() -> None:
    original = (
        'Please please update "./docs/plan.md" on 2026-05-31 with "ship it", '
        "then run /checkonline and keep [provided message_payload payload]."
    )
    candidate = (
        'Update "./docs/plan.md" on 2026-05-31 with "ship it", then run '
        "/checkonline and keep [provided message_payload payload]."
    )
    proposal = PromptRephraseProposal(
        rephrased_prompt=candidate,
        changed=True,
        preserved_requirements=["path", "date", "quoted text", "macro", "payload"],
        removed_duplication=["duplicated please"],
        reason="The original was repetitive.",
        confidence=0.91,
    )

    outcome = validate_prompt_rephrase(original, proposal)

    assert outcome.applied is True
    assert dropped_protected_spans(original, candidate) == []


def test_prompt_rephrase_falls_back_for_empty_low_confidence_and_dropped_placeholders() -> None:
    original = 'Use [provided message_payload payload] at "./docs/plan.md".'
    empty = PromptRephraseProposal(
        rephrased_prompt="",
        changed=True,
        preserved_requirements=[],
        removed_duplication=[],
        reason="No safe rewrite.",
        confidence=0.95,
    )
    low_confidence = empty.model_copy(
        update={"rephrased_prompt": "Use the payload.", "confidence": 0.2}
    )
    dropped_placeholder = empty.model_copy(
        update={
            "rephrased_prompt": 'Use the payload at "./docs/plan.md".',
            "confidence": 0.95,
        }
    )

    assert validate_prompt_rephrase(original, empty).fallback_reason == "empty_output"
    assert validate_prompt_rephrase(original, low_confidence).fallback_reason == "low_confidence"
    assert (
        validate_prompt_rephrase(original, dropped_placeholder).fallback_reason
        == "dropped_protected_content"
    )


def test_prompt_rephrase_falls_back_for_invalid_schema() -> None:
    outcome = rephrase_prompt_preflight("explain hello world", QueueLLM({"not": "valid"}))

    assert outcome.applied is False
    assert outcome.rephrased_prompt == "explain hello world"
    assert outcome.fallback_reason == "llm_schema_failure:schema_validation_error"


def test_prompt_rephrase_effective_setting_prefers_public_key_then_legacy() -> None:
    assert effective_prompt_rephrase_enabled({}) is True
    assert effective_prompt_rephrase_enabled({"operator_auto_rephrase_retry_enabled": False}) is False
    assert (
        effective_prompt_rephrase_enabled(
            {
                "prompt_rephrase_enabled": True,
                "operator_auto_rephrase_retry_enabled": False,
            }
        )
        is True
    )
    legacy_config = RuntimeConfig(operator_auto_rephrase_retry_enabled=False)
    assert effective_prompt_rephrase_enabled(legacy_config) is False
    public_config = RuntimeConfig(
        prompt_rephrase_enabled=True,
        operator_auto_rephrase_retry_enabled=False,
    )
    assert effective_prompt_rephrase_enabled(public_config) is True
