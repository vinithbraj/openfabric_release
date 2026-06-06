from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_runtime.capabilities import build_default_registry
from agent_runtime.core.orchestrator import AgentRuntime
from agent_runtime.execution.engine import ExecutionEngine
from agent_runtime.execution.result_store import InMemoryResultStore
from agent_runtime.memory import AgentMemoryStore, MemoryEntryCreate
from agent_runtime.output_pipeline.orchestrator import OutputPipelineOrchestrator
from agent_runtime.profiling import RuntimeProfiler, latency_distribution


class DirectAnswerLLMClient:
    """Small fake client for profiling the no-tool path."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.model = "fake-direct-answer"
        self.temperature = 0.0

    def complete_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        _ = schema
        self.prompts.append(prompt)
        lowered = prompt.lower()
        if "non-authoritative prompt normalization" in lowered:
            return {
                "rephrased_prompt": "what is the capital of France?",
                "changed": False,
                "preserved_requirements": ["capital of France"],
                "removed_duplication": [],
                "reason": "The prompt is already clear.",
                "confidence": 0.95,
            }
        if "classifying a user prompt" in lowered:
            return {
                "prompt_type": "simple_question",
                "requires_tools": False,
                "likely_domains": ["general"],
                "risk_level": "low",
                "needs_clarification": False,
                "clarification_question": None,
                "reason": "This can be answered without runtime tools.",
                "confidence": 0.95,
                "assumptions": [],
            }
        if "directly answering a user prompt" in lowered:
            if "memory_helped_direct_answer" in lowered:
                return {
                    "answer": "MEMORY_HELPED_DIRECT_ANSWER",
                    "confidence": 0.95,
                    "reason": "Persistent memory supplied an exact answer rule.",
                }
            return {
                "answer": "Paris is the capital of France.",
                "confidence": 0.95,
                "reason": "This is stable general knowledge.",
            }
        raise AssertionError(f"Unexpected prompt: {prompt[:120]}")


def _direct_answer_runtime(tmp_path: Path, memory_store: AgentMemoryStore | None = None) -> AgentRuntime:
    registry = build_default_registry()
    engine = ExecutionEngine(
        registry,
        {
            "workspace_root": str(tmp_path),
            "allow_shell_execution": False,
            "allow_network_operations": False,
            "gateway_url": "http://gateway",
        },
        InMemoryResultStore(),
    )
    return AgentRuntime(
        DirectAnswerLLMClient(),
        registry,
        engine,
        OutputPipelineOrchestrator(),
        memory_store=memory_store,
    )


def test_latency_distribution_reports_average_percentiles_and_max() -> None:
    stats = latency_distribution([10.0, 20.0, 30.0, 40.0])

    assert stats["count"] == 4
    assert stats["average_ms"] == 25.0
    assert stats["p50_ms"] in {20.0, 30.0}
    assert stats["p95_ms"] == 40.0
    assert stats["max_ms"] == 40.0


def test_runtime_profiler_summarizes_repeated_llm_stages() -> None:
    profiler = RuntimeProfiler("req-profile-test")
    profiler.record_llm_call(
        stage="argument_extraction",
        schema_name="ArgumentExtractionProposal",
        model="fake",
        prompt_chars=100,
        schema_chars=50,
        input_tokens_estimate=38,
        output_tokens_estimate=10,
        duration_ms=12.0,
        status="ok",
    )
    profiler.record_llm_call(
        stage="argument_extraction",
        schema_name="ArgumentExtractionProposal",
        model="fake",
        prompt_chars=100,
        schema_chars=50,
        input_tokens_estimate=38,
        output_tokens_estimate=10,
        duration_ms=14.0,
        status="ok",
    )

    summary = profiler.summary()

    assert summary["llm_call_count"] == 2
    assert summary["llm_retry_count"] == 0
    assert summary["llm_retry_like_count"] == 1
    assert summary["llm_retry_groups"] == [
        {
            "stage": "argument_extraction",
            "schema_name": "ArgumentExtractionProposal",
            "call_count": 2,
        }
    ]
    assert summary["deterministic_opportunities"][0]["type"] == "repeated_semantic_stage"


def test_direct_answer_profile_records_stage_timings_and_llm_calls(tmp_path: Path) -> None:
    runtime = _direct_answer_runtime(tmp_path)

    response = runtime.handle_request(
        "what is the capital of France?",
        {"workspace_root": str(tmp_path), "llm_context_window_tokens": 10000},
    )

    assert "Paris" in response
    assert runtime.last_profile_summary is not None
    summary = runtime.last_profile_summary
    assert summary["llm_call_count"] == 3
    assert summary["llm_calls_by_stage"]["prompt_rephrase"] == 1
    assert summary["llm_calls_by_stage"]["prompt_classification"] == 1
    assert summary["llm_calls_by_stage"]["direct_answer"] == 1
    assert summary["longest_stage"] is not None
    assert any(item["stage"] == "prompt_classification" for item in summary["stage_timings"])
    assert runtime.last_planning_trace is not None
    assert runtime.last_planning_trace.metadata["runtime_profile"]["llm_call_count"] == 3
    context_usage = runtime.last_planning_trace.metadata["runtime_profile"]["llm_context_usage"]
    assert context_usage["context_window_tokens"] == 10000
    assert context_usage["remaining_percent"] <= 100
    assert context_usage["used_tokens_estimate"] > 0


def test_direct_answer_prompt_uses_retrieved_memory(tmp_path: Path) -> None:
    memory_store = AgentMemoryStore(tmp_path / "agent_memory.db")
    memory_store.create_entry(
        MemoryEntryCreate(
            instruction=(
                "When user asks marker OF_MEM_DIRECT_ANSWER, answer exactly MEMORY_HELPED_DIRECT_ANSWER."
            ),
            summary="Direct answer marker rule.",
            scope="global",
        )
    )
    runtime = _direct_answer_runtime(tmp_path, memory_store)

    response = runtime.handle_request("OF_MEM_DIRECT_ANSWER")

    assert response == "MEMORY_HELPED_DIRECT_ANSWER"
    assert runtime.last_planning_trace is not None
    assert runtime.last_planning_trace.metadata["agent_memory_count"] == 1
